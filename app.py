import sqlite3
import re
import os
import subprocess
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import quote

from flask import Flask, render_template, request, send_from_directory, Response

app = Flask(__name__, template_folder='web_pages', static_folder='web_pages/assets')

# --- Sécurité : identifiants admin via variables d'environnement ---
ADMIN_USER = os.environ.get('ADMIN_USER', 'admin')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'changez-moi-imperativement')
BASE_URL = os.environ.get('BASE_URL', 'https://smart-padel-price.fr')

MARQUES_LISTE = [
    "Adidas", "Acox", "Activ", "Agatha Ruiz de la Prada", "Aim Padel", "Akkeron", "Alacrán", "Ares", "Arrow Point", "Artengo", "Asics", "Attraction",
    "Babolat", "Black Crown", "Blackthunder", "Boomerang", "Bullpadel",
    "Cartri", "Cazzec", "Celtika", "Cork Padel", "Cube", "Cygnus",
    "Dabber", "Diadora", "Donnay", "Drop Shot", "Dunlop", "Duruss",
    "Eclypse", "Fakun", "Fila", "Head", "Hirostar",
    "JHayber", "Joma", "JustTen", "Kaitt", "Kombat Padel", "Kuikma", "Kuumax",
    "Lacoste", "Lok", "Macron", "Middlemoon", "Mystica", "Nox", "Osaka", "Oxdog", "Orygen",
    "Padel Coach", "Puma", "Prince Padel", "Pro Kennex",
    "Royal Padel", "Sane", "Siux", "Slazenger", "Softee", "StarVie", "Steel Custom",
    "Tecnifibre", "Vairo", "Varlion", "Vibora / Vibor-A", "Volt",
    "Wilson", "Yonex"
]

def require_admin(f):
    """Protège les routes /admin par une authentification HTTP Basic."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or auth.username != ADMIN_USER or auth.password != ADMIN_PASSWORD:
            return Response(
                "Accès réservé à l'administrateur.",
                401,
                {'WWW-Authenticate': 'Basic realm="SmartPadelPrice Admin"'}
            )
        return f(*args, **kwargs)
    return decorated

def clean_price_val(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    cleaned = re.sub(r'[^\d.,]', '', str(val)).replace(',', '.')
    try:
        return float(cleaned)
    except ValueError:
        return None

@app.template_filter('prix')
def format_prix(val):
    """Formate un prix au format français : 149.9 -> '149,90'."""
    p = clean_price_val(val)
    if p is None:
        return 'N/C'
    s = f"{p:.2f}".rstrip('0').rstrip('.')
    return s.replace('.', ',')

def get_db_connection():
    conn = sqlite3.connect('padel_comparator.db')
    conn.row_factory = sqlite3.Row
    return conn

def normaliser_pour_regroupement(nom, marque, url="", categorie="raquette"):
    if not nom: 
        return url.split('/')[-1] if url else ""

    texte = nom.lower()
    if marque: 
        texte = texte.replace(marque.lower(), "")

    parasites = [
        "raquette", "de", "padel", "pala", "balle", "balles", "tubo", "bote", "edition", "exclusive", 
        "by", "agustin", "tapia", "alum", "pack", "line", "ltd", "v2",
        "2023", "2024", "2025", "2026", "2027"
    ]

    for p in parasites:
        texte = re.sub(r'\b' + re.escape(p) + r'\b', ' ', texte, flags=re.IGNORECASE)

    pur = re.sub(r'[^a-z0-9]', '', texte)
    if len(pur) < 3 and url:
        pur += "_" + url.split('/')[-1]
    return pur

def obtenir_toutes_les_marques(selected_cats=None):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        if not selected_cats:
            selected_cats = ['raquettes', 'balles']

        marques_set = set()

        if 'raquettes' in selected_cats:
            cursor.execute("SELECT DISTINCT marque FROM produits WHERE marque IS NOT NULL AND marque != ''")
            for row in cursor.fetchall():
                if row['marque']:
                    marques_set.add(row['marque'].upper())

        if 'balles' in selected_cats:
            cursor.execute("SELECT DISTINCT marque FROM produits_balles WHERE marque IS NOT NULL AND marque != ''")
            for row in cursor.fetchall():
                if row['marque']:
                    marques_set.add(row['marque'].upper())

        conn.close()

        marques_liste_triee = sorted(list(marques_set))
        return marques_liste_triee if marques_liste_triee else sorted([m.upper() for m in MARQUES_LISTE])
    except Exception:
        return sorted([m.upper() for m in MARQUES_LISTE])

@app.route('/')
@app.route('/index.html')
def index():
    return render_template('index.html')

@app.route('/comparateur')
@app.route('/comparateur.html')
def comparateur():
    selected_cats = request.args.getlist('cat')
    if not selected_cats:
        selected_cats = ['raquettes']

    liste_marques = obtenir_toutes_les_marques(selected_cats)

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        search_query = request.args.get('q', '').strip()
        selected_marques = request.args.getlist('marque')
        sort_option = request.args.get('sort', 'default').strip()

        single_marque = request.args.get('marque', '').strip()
        if single_marque and single_marque not in selected_marques:
            selected_marques.append(single_marque)

        prix_min_filter = request.args.get('prix_min', type=float, default=0.0)

        if 'prix_max' in request.args:
            prix_max_filter = request.args.get('prix_max', type=float, default=800.0)
        else:
            if selected_marques or sort_option == 'desc':
                prix_max_filter = 2500.0
            else:
                prix_max_filter = 800.0

        tables_a_interroger = []
        if 'raquettes' in selected_cats:
            tables_a_interroger.append(('raquettes', 'produits', 'raquette'))
        if 'balles' in selected_cats:
            tables_a_interroger.append(('balles', 'produits_balles', 'balle'))

        lignes = []
        for table_offre, table_prod, cat_nom in tables_a_interroger:
            query = f"SELECT r.*, p.nom_produit, p.marque, p.url_image FROM {table_offre} r LEFT JOIN {table_prod} p ON r.produit_id = p.id WHERE 1=1"
            params = []

            if search_query:
                mots = search_query.split()
                for mot in mots:
                    query += " AND (p.nom_produit LIKE ? OR p.marque LIKE ? OR r.site_marchand LIKE ?)"
                    params.extend([f"%{mot}%", f"%{mot}%", f"%{mot}%"])

            if selected_marques:
                placeholders = ', '.join(['?'] * len(selected_marques))
                query += f" AND UPPER(p.marque) IN ({placeholders})"
                params.extend([m.upper() for m in selected_marques])

            res = cursor.execute(query, params).fetchall()
            for r_row in res:
                row_dict = dict(r_row)
                row_dict['categorie_type'] = cat_nom
                lignes.append(row_dict)

    finally:
        conn.close()

    produits_dict = {}
    for item in lignes:
        nom_produit = item.get('nom_produit') or 'Produit Inconnu'
        marque_produit = (item.get('marque') or 'Autre').upper()
        url_produit = item.get('url_produit', '')
        cat_type = item.get('categorie_type', 'raquette')

        nom_norm = normaliser_pour_regroupement(nom_produit, marque_produit, url_produit, cat_type)
        cle_regroupement = f"{cat_type}_{marque_produit.lower()}_{nom_norm}"

        if cle_regroupement not in produits_dict:
            produits_dict[cle_regroupement] = {
                'marque': marque_produit, 
                'nom': nom_produit, 
                'image': item.get('url_image'), 
                'offres': []
            }

        if not produits_dict[cle_regroupement]['image'] and item.get('url_image'):
            produits_dict[cle_regroupement]['image'] = item.get('url_image')

        prix_final = item.get('meilleur_prix') if item.get('meilleur_prix') is not None else item.get('prix_base')
        site_actuel = item.get('site_marchand', 'Marchand')

        existing_offer = next((o for o in produits_dict[cle_regroupement]['offres'] if o['site'] == site_actuel), None)

        if existing_offer is None:
            produits_dict[cle_regroupement]['offres'].append({
                'site': site_actuel, 
                'prix': prix_final, 
                'url': url_produit, 
                'code_promo': item.get('code_gagnant')
            })
        else:
            prix_existant = clean_price_val(existing_offer.get('prix'))
            nouveau_prix = clean_price_val(prix_final)
            if nouveau_prix is not None and (prix_existant is None or nouveau_prix < prix_existant):
                existing_offer['prix'] = prix_final
                existing_offer['url'] = url_produit
                existing_offer['code_promo'] = item.get('code_gagnant')

    produits_filtres = []
    for prod in produits_dict.values():
        offres = prod['offres']

        def get_prix_num(o):
            p = clean_price_val(o.get('prix'))
            return p if p is not None else float('inf')

        prod['offres'].sort(key=get_prix_num)

        prix_valides = []
        for o in offres:
            p_clean = clean_price_val(o['prix'])
            if p_clean is not None and p_clean > 0:
                prix_valides.append(p_clean)

        min_p = min(prix_valides) if prix_valides else None
        prod['prix_min'] = min_p

        if min_p is not None and (prix_min_filter <= min_p <= prix_max_filter):
            produits_filtres.append(prod)
        elif min_p is None and prix_min_filter == 0.0:
            produits_filtres.append(prod)

    if sort_option == 'asc':
        produits_filtres.sort(key=lambda x: x['prix_min'] if x['prix_min'] is not None else float('inf'))
    elif sort_option == 'desc':
        produits_filtres.sort(key=lambda x: x['prix_min'] if x['prix_min'] is not None else -1, reverse=True)

    return render_template(
        'comparateur.html', 
        produits=produits_filtres, 
        marques=liste_marques, 
        search_query=search_query,
        prix_min=int(prix_min_filter),
        prix_max=int(prix_max_filter),
        sort_option=sort_option
    )

# ==========================================================
# SEO : robots.txt et sitemap.xml (les deux URLs qui
# renvoyaient 404 et bloquaient l'indexation)
# ==========================================================

@app.route('/robots.txt')
def robots_txt():
    lignes = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin",
        "",
        f"Sitemap: {BASE_URL}/sitemap.xml",
        "",
    ]
    return Response("\n".join(lignes), mimetype='text/plain')


@app.route('/sitemap.xml')
def sitemap_xml():
    urls = [
        {'loc': f'{BASE_URL}/', 'priority': '1.0'},
        {'loc': f'{BASE_URL}/comparateur.html', 'priority': '0.9'},
        {'loc': f'{BASE_URL}/marques.html', 'priority': '0.8'},
    ]
    try:
        conn = get_db_connection()
        rows = conn.execute(
            "SELECT DISTINCT marque FROM produits WHERE marque IS NOT NULL AND marque != '' ORDER BY marque"
        ).fetchall()
        conn.close()
        for row in rows:
            marque = row['marque']
            if marque:
                urls.append({
                    'loc': f"{BASE_URL}/comparateur.html?marque={quote(marque)}",
                    'priority': '0.7'
                })
    except Exception:
        pass

    lastmod = datetime.now(timezone.utc).date().isoformat()
    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        xml.append(
            f"  <url><loc>{u['loc']}</loc><lastmod>{lastmod}</lastmod>"
            f"<changefreq>daily</changefreq><priority>{u['priority']}</priority></url>"
        )
    xml.append('</urlset>')
    return Response("\n".join(xml), mimetype='application/xml')

# ==========================================================

@app.route('/admin')
@require_admin
def admin_panel():
    return render_template('admin.html')

@app.route('/admin/run/<script_type>/<script_name>', methods=['POST'])
@require_admin
def run_script(script_type, script_name):
    # Contre le path traversal : nom de script strictement limité
    if script_type not in ('catalogues', 'prix') or not re.fullmatch(r'[\w\-]+', script_name):
        return "Requête invalide", 400

    mode_execution = request.form.get('mode', 'TEST')
    if script_type == 'catalogues': 
        chemin = f"scrappers/{script_name}.py"
        args = ["python", chemin]
    else: 
        chemin = f"scrappers/scrapers_prix/{script_name}.py"
        args = ["python", chemin, mode_execution]

    try:
        subprocess.Popen(args)
    except Exception as e: 
        print(f"Erreur : {e}")

    return f'<script>alert("Le script {script_name} a été lancé avec succès !"); window.location.href = "/admin";</script>'

@app.route('/web_pages/<path:filename>')
def serve_web_pages(filename):
    return send_from_directory('web_pages', filename)

@app.route('/<path:filename>')
def serve_root_files(filename):
    if os.path.exists(os.path.join('web_pages', filename)):
        return send_from_directory('web_pages', filename)
    return "Page non trouvée", 404

if __name__ == '__main__':
    # FLASK_DEBUG=1 pour le dev, rien (ou autre valeur) en production
    app.run(debug=os.environ.get('FLASK_DEBUG') == '1')
