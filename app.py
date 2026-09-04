import sqlite3
import re
import subprocess
from flask import Flask, render_template, request

app = Flask(__name__, template_folder='web_pages', static_folder='web_pages/assets')

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

def clean_price_val(val):
    """Convertit proprement une valeur de prix (float, int, str) en float."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    cleaned = re.sub(r'[^\d.,]', '', str(val)).replace(',', '.')
    try:
        return float(cleaned)
    except ValueError:
        return None

def get_db_connection():
    conn = sqlite3.connect('padel_comparator.db')
    conn.row_factory = sqlite3.Row
    return conn

def nettoyer_noms_produits():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, nom_produit FROM produits")
    produits = cursor.fetchall()
    
    termes_a_retirer = [
        r"\s*raquette\s+de\s+padel\s*",
        r"\s*raquette\s+padel\s*",
        r"\s*raquette\s*",
        r"\s*pala\s+de\s+padel\s*",
        r"\s*default\s+title\s*"
    ]
    
    for prod_id, nom in produits:
        if not nom: continue
        nouveau_nom = nom
        for motif in termes_a_retirer:
            nouveau_nom = re.sub(motif, " ", nouveau_nom, flags=re.IGNORECASE)
        nouveau_nom = re.sub(r'\s+', ' ', nouveau_nom).strip()
        nouveau_nom = re.sub(r'^[-–—:]+\s*|\s*[-–—:]+$', '', nouveau_nom).strip()
        if nouveau_nom != nom:
            cursor.execute("UPDATE produits SET nom_produit = ? WHERE id = ?", (nouveau_nom, prod_id))
    conn.commit()
    conn.close()

def normaliser_pour_regroupement(nom, marque, url):
    """Génère une empreinte unique en intégrant l'année (2026 par défaut si absente)."""
    if not nom: return ""
    
    texte_complet = f"{nom} {url}".lower()
    
    annee_match = re.search(r'\b(202\d|203\d)\b', texte_complet)
    annee_str = annee_match.group(1) if annee_match else "2026"
    
    texte = nom.lower()
    if marque: texte = texte.replace(marque.lower(), "")
        
    parasites = ["raquette", "de", "padel", "pala", "edition", "exclusive", "by", "agustin", "tapia", "alum", "pack", "line"]
    for p in parasites: texte = texte.replace(p, "")
    
    if annee_str in texte: texte = texte.replace(annee_str, "")
        
    pur = re.sub(r'[^a-z0-9]', '', texte)
    return f"{pur}_{annee_str}"

def mettre_a_jour_marques():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, nom_produit FROM produits WHERE marque IS NULL OR marque = ''")
    produits = cursor.fetchall()
    
    for prod_id, nom_produit in produits:
        if not nom_produit: continue
        nom_lower = nom_produit.lower()
        for marque in sorted(MARQUES_LISTE, key=len, reverse=True):
            pattern = r'\b' + re.escape(marque.lower()) + r'\b'
            if re.search(pattern, nom_lower):
                cursor.execute("UPDATE produits SET marque = ? WHERE id = ?", (marque, prod_id))
                break
    conn.commit()
    conn.close()

def obtenir_toutes_les_marques():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT marque FROM produits WHERE marque IS NOT NULL AND marque != '' ORDER BY marque ASC")
        rows = cursor.fetchall()
        conn.close()
        marques_bd = [row['marque'] for row in rows]
        return marques_bd if marques_bd else sorted(MARQUES_LISTE)
    except Exception:
        return sorted(MARQUES_LISTE)

def supprimer_doublons_sql():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM raquettes WHERE id NOT IN (SELECT MIN(id) FROM raquettes GROUP BY url_produit)")
    nb_supprimes = cursor.rowcount
    conn.commit()
    conn.close()
    return nb_supprimes

@app.route('/')
@app.route('/index.html')
def index():
    return render_template('index.html')

@app.route('/comparateur')
@app.route('/comparateur.html')
def comparateur():
    nettoyer_noms_produits()
    mettre_a_jour_marques()
    liste_marques = obtenir_toutes_les_marques()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Récupération des filtres et du Tri
    search_query = request.args.get('q', '').strip()
    selected_cats = request.args.getlist('cat')
    selected_marques = request.args.getlist('marque')
    sort_option = request.args.get('sort', 'default').strip() # 'asc', 'desc', ou 'default'
    
    single_marque = request.args.get('marque', '').strip()
    if single_marque and single_marque not in selected_marques:
        selected_marques.append(single_marque)

    prix_min_filter = request.args.get('prix_min', type=float, default=0.0)
    prix_max_filter = request.args.get('prix_max', type=float, default=800.0)

    # 2. Requête SQL
    query = "SELECT r.*, p.nom_produit, p.marque, p.url_image FROM raquettes r LEFT JOIN produits p ON r.produit_id = p.id WHERE 1=1"
    params = []
    
    if search_query:
        query += " AND (p.nom_produit LIKE ? OR p.marque LIKE ? OR r.site_marchand LIKE ?)"
        params.extend([f"%{search_query}%", f"%{search_query}%", f"%{search_query}%"])
        
    if selected_marques:
        placeholders = ', '.join(['?'] * len(selected_marques))
        query += f" AND p.marque IN ({placeholders})"
        params.extend(selected_marques)

    if selected_cats and 'raquettes' not in selected_cats:
        lignes = []
    else:
        lignes = cursor.execute(query, params).fetchall()
    conn.close()

    # 3. Regroupement des offres
    produits_dict = {}
    for row in lignes:
        item = dict(row)
        nom_produit = item.get('nom_produit') or 'Produit Inconnu'
        marque_produit = item.get('marque') or 'Autre'
        url_produit = item.get('url_produit', '')
        
        nom_norm = normaliser_pour_regroupement(nom_produit, marque_produit, url_produit)
        cle_regroupement = f"{marque_produit.lower()}_{nom_norm}"
        
        if cle_regroupement not in produits_dict:
            produits_dict[cle_regroupement] = {
                'marque': marque_produit, 'nom': nom_produit, 'image': item.get('url_image'), 'offres': []
            }
        
        if not produits_dict[cle_regroupement]['image'] and item.get('url_image'):
            produits_dict[cle_regroupement]['image'] = item.get('url_image')
        
        prix_final = item.get('meilleur_prix') if item.get('meilleur_prix') is not None else item.get('prix_base')
        sites_deja_presents = [o['site'] for o in produits_dict[cle_regroupement]['offres']]
        site_actuel = item.get('site_marchand', 'Marchand')
        
        if site_actuel not in sites_deja_presents:
            produits_dict[cle_regroupement]['offres'].append({
                'site': site_actuel, 'prix': prix_final, 'url': url_produit, 'code_promo': item.get('code_gagnant')
            })

    # 4. Calcul du prix minimal, tri des offres par prix et filtrage
    produits_filtres = []
    for prod in produits_dict.values():
        offres = prod['offres']
        
        # Tri des offres de la modale du moins cher au plus cher
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

    # 5. Tri des produits par prix
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

@app.route('/admin')
def admin_panel():
    return render_template('admin.html')

@app.route('/admin/nettoyer_base', methods=['POST'])
def nettoyer_base():
    nb = supprimer_doublons_sql()
    return f'<script>alert("Maintenance terminée : {nb} doublons supprimés !"); window.location.href = "/admin";</script>'

@app.route('/admin/run/<script_type>/<script_name>', methods=['POST'])
def run_script(script_type, script_name):
    mode_execution = request.form.get('mode', 'TEST')

    if script_type == 'catalogues': 
        chemin = f"scrappers/{script_name}.py"
        args = ["python", chemin]
    elif script_type == 'prix': 
        chemin = f"scrappers/scrapers_prix/{script_name}.py"
        args = ["python", chemin, mode_execution]
    elif script_type == 'global':
        if script_name in ['lancer_tous_les_catalogues', 'lancer_tous_les_prix']: 
            chemin = f"scrappers/{script_name}.py"
            args = ["python", chemin]
        else: 
            return "Script global non autorisé", 403
    else: 
        return "Type de script invalide", 400
        
    try:
        subprocess.Popen(args)
    except Exception as e: 
        print(f"Erreur : {e}")
        
    return f'<script>alert("Le script {script_name} a été lancé avec succès en mode {mode_execution} !"); window.location.href = "/admin";</script>'

if __name__ == '__main__':
    app.run(debug=True)