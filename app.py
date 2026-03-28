from flask import Flask, render_template, request, redirect, url_for, abort, flash
import sqlite3
import secrets
import socket
import os

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(16))
ADMIN_TOKEN = os.environ.get('ADMIN_TOKEN', 'pasen2026admin')
DATABASE_URL = os.environ.get('DATABASE_URL')  # Render zet dit automatisch

# ── Database-abstractie: werkt met zowel SQLite (lokaal) als PostgreSQL (Render) ──

if DATABASE_URL:
    import psycopg2
    import psycopg2.extras

    class DbConn:
        """Wrapper zodat psycopg2 dezelfde API heeft als sqlite3."""
        def __init__(self):
            self._conn = psycopg2.connect(
                DATABASE_URL,
                cursor_factory=psycopg2.extras.RealDictCursor
            )

        def execute(self, query, params=()):
            query = query.replace('?', '%s')   # SQLite → PostgreSQL placeholders
            cur = self._conn.cursor()
            cur.execute(query, params)
            return cur

        def __enter__(self):
            return self

        def __exit__(self, exc_type, *_):
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
            self._conn.close()

    def get_db():
        return DbConn()

    DB_ERROR = psycopg2.IntegrityError

    def init_db():
        with get_db() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS gezinnen (
                    id SERIAL PRIMARY KEY,
                    naam TEXT NOT NULL,
                    token TEXT UNIQUE NOT NULL
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS bijdragen (
                    id SERIAL PRIMARY KEY,
                    gezin_id INTEGER NOT NULL REFERENCES gezinnen(id) ON DELETE CASCADE,
                    categorie TEXT NOT NULL,
                    omschrijving TEXT NOT NULL
                )
            ''')

else:
    DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(__file__), 'pasen.db'))

    def get_db():
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    DB_ERROR = sqlite3.IntegrityError

    def init_db():
        with get_db() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS gezinnen (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    naam TEXT NOT NULL,
                    token TEXT UNIQUE NOT NULL
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS bijdragen (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    gezin_id INTEGER NOT NULL,
                    categorie TEXT NOT NULL,
                    omschrijving TEXT NOT NULL,
                    FOREIGN KEY (gezin_id) REFERENCES gezinnen(id) ON DELETE CASCADE
                )
            ''')


# ── Hulpfuncties ──

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


def laad_overzicht():
    with get_db() as conn:
        gezinnen = conn.execute('SELECT * FROM gezinnen ORDER BY naam').fetchall()
        bijdragen = conn.execute('SELECT * FROM bijdragen').fetchall()

    data = {}
    for g in gezinnen:
        data[g['id']] = {
            'naam': g['naam'],
            'token': g['token'],
            'eten': [],
            'drinken': []
        }
    for b in bijdragen:
        if b['gezin_id'] in data:
            data[b['gezin_id']][b['categorie']].append(b['omschrijving'])

    return data


# ── Routes ──

@app.route('/')
def index():
    data = laad_overzicht()
    base_url = request.url_root.rstrip('/')
    return render_template('index.html', data=data, base_url=base_url)


@app.route('/aanmelden', methods=['GET', 'POST'])
def aanmelden():
    if request.method == 'POST':
        naam = request.form.get('naam', '').strip()
        if naam:
            token = secrets.token_urlsafe(12)
            try:
                with get_db() as conn:
                    conn.execute('INSERT INTO gezinnen (naam, token) VALUES (?, ?)', (naam, token))
                flash(f'Gezin "{naam}" is aangemeld! Bewaar de link om later aan te passen.', 'success')
                return redirect(url_for('bewerk', token=token))
            except DB_ERROR:
                flash('Er is een fout opgetreden. Probeer opnieuw.', 'error')
        else:
            flash('Vul een gezinsnaam in.', 'error')
    return render_template('aanmelden.html')


@app.route('/bewerk/<token>', methods=['GET', 'POST'])
def bewerk(token):
    with get_db() as conn:
        gezin = conn.execute('SELECT * FROM gezinnen WHERE token = ?', (token,)).fetchone()
        if not gezin:
            abort(404)

        if request.method == 'POST':
            conn.execute('DELETE FROM bijdragen WHERE gezin_id = ?', (gezin['id'],))
            for item in request.form.getlist('eten[]'):
                if item.strip():
                    conn.execute(
                        'INSERT INTO bijdragen (gezin_id, categorie, omschrijving) VALUES (?, ?, ?)',
                        (gezin['id'], 'eten', item.strip())
                    )
            for item in request.form.getlist('drinken[]'):
                if item.strip():
                    conn.execute(
                        'INSERT INTO bijdragen (gezin_id, categorie, omschrijving) VALUES (?, ?, ?)',
                        (gezin['id'], 'drinken', item.strip())
                    )
            flash('Bijdragen opgeslagen!', 'success')
            return redirect(url_for('index'))

        bijdragen = conn.execute(
            'SELECT * FROM bijdragen WHERE gezin_id = ?', (gezin['id'],)
        ).fetchall()

    eten = [b['omschrijving'] for b in bijdragen if b['categorie'] == 'eten']
    drinken = [b['omschrijving'] for b in bijdragen if b['categorie'] == 'drinken']
    return render_template('bewerk.html', gezin=gezin, eten=eten, drinken=drinken, token=token)


@app.route(f'/admin/{ADMIN_TOKEN}')
def admin():
    data = laad_overzicht()
    base_url = request.url_root.rstrip('/')
    return render_template('admin.html', data=data, base_url=base_url, admin_token=ADMIN_TOKEN)


@app.route(f'/admin/{ADMIN_TOKEN}/toevoegen', methods=['POST'])
def admin_toevoegen():
    naam = request.form.get('naam', '').strip()
    eten_items = [x.strip() for x in request.form.get('eten', '').split('\n') if x.strip()]
    drinken_items = [x.strip() for x in request.form.get('drinken', '').split('\n') if x.strip()]

    if naam:
        token = secrets.token_urlsafe(12)
        try:
            with get_db() as conn:
                conn.execute('INSERT INTO gezinnen (naam, token) VALUES (?, ?)', (naam, token))
                gezin = conn.execute('SELECT id FROM gezinnen WHERE token = ?', (token,)).fetchone()
                for item in eten_items:
                    conn.execute(
                        'INSERT INTO bijdragen (gezin_id, categorie, omschrijving) VALUES (?, ?, ?)',
                        (gezin['id'], 'eten', item)
                    )
                for item in drinken_items:
                    conn.execute(
                        'INSERT INTO bijdragen (gezin_id, categorie, omschrijving) VALUES (?, ?, ?)',
                        (gezin['id'], 'drinken', item)
                    )
            flash(f'Gezin "{naam}" toegevoegd!', 'success')
        except DB_ERROR:
            flash('Fout bij toevoegen.', 'error')
    return redirect(url_for('admin'))


@app.route(f'/admin/{ADMIN_TOKEN}/verwijder/<int:gezin_id>', methods=['POST'])
def admin_verwijder(gezin_id):
    with get_db() as conn:
        gezin = conn.execute('SELECT naam FROM gezinnen WHERE id = ?', (gezin_id,)).fetchone()
        if gezin:
            conn.execute('DELETE FROM bijdragen WHERE gezin_id = ?', (gezin_id,))
            conn.execute('DELETE FROM gezinnen WHERE id = ?', (gezin_id,))
            flash(f'Gezin "{gezin["naam"]}" verwijderd.', 'success')
    return redirect(url_for('admin'))


# Init database bij opstarten (ook via gunicorn)
init_db()

if __name__ == '__main__':
    ip = get_local_ip()
    port = int(os.environ.get('PORT', 5001))
    print(f'\n🐣 Pasen App gestart!')
    print(f'   Lokaal:   http://127.0.0.1:{port}')
    print(f'   Netwerk:  http://{ip}:{port}')
    print(f'   Admin:    http://{ip}:{port}/admin/{ADMIN_TOKEN}')
    print(f'\nDeel de netwerk-URL met familie (zorg dat je op hetzelfde wifi-netwerk zit)\n')
    app.run(host='0.0.0.0', port=port, debug=False)
