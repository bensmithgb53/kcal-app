from flask import Flask, render_template, request, session, redirect, url_for, flash
import requests
from bs4 import BeautifulSoup
import re
import logging
from logging.handlers import RotatingFileHandler
import os
import time
import random

app = Flask(__name__)
app.secret_key = "stable_key_v32_home_reset_fix"

# --- LOGGING SETUP ---
if not os.path.exists('logs'): os.mkdir('logs')
handler = RotatingFileHandler('logs/nutrition_app.log', maxBytes=5*1024*1024, backupCount=5)
handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s - %(pathname)s:%(lineno)d'))
logger = logging.getLogger('nutrition')
logger.setLevel(logging.INFO)
logger.addHandler(handler)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
logger.addHandler(console)

# --- STEALTH CONFIG ---
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.1 Safari/605.1.15"
]
scraper_session = requests.Session()

def make_stealth_request(url):
    time.sleep(random.uniform(1.5, 3.5))
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
        "Referer": "https://www.google.com/"
    }
    return scraper_session.get(url, headers=headers, timeout=15)

def get_product_details(product_url):
    try:
        full_url = "https://www.nutracheck.co.uk" + product_url
        res = make_stealth_request(full_url)
        if res.status_code == 404: return "404"
        soup = BeautifulSoup(res.text, 'html.parser')

        best_id, best_score = 'prodDetails2', 0
        dropdown = soup.find('select', {'onchange': re.compile(r'switchServing', re.I)})
        if dropdown:
            for opt in dropdown.find_all('option'):
                txt = opt.get_text().strip().lower()
                val = opt.get('value')
                score = 0
                if re.match(r'^(?:per\s+)?100\s*[gm]', txt): score += 100
                elif '100' in txt and 'g' in txt: score += 50
                if any(x in txt for x in ['pack', 'serving', 'portion']): score -= 20
                if score > best_score: best_score = score; best_id = val

        container = soup.find('div', id=best_id)
        if not container or not container.get_text(strip=True):
            for c in soup.find_all('div', class_='showps'):
                if '100' in c.get_text() and 'g' in c.get_text(): container = c; break
        if not container:
            el = soup.find(string=re.compile(r'Energy:', re.I))
            if el: container = el.find_parent()
        if not container: return None

        data_map = {"kcal": "0", "protein": "0", "carbs": "0", "fat": "0"}
        table = container.find('table')
        if table:
            for row in table.find_all('tr'):
                cells = row.find_all(['td', 'th'])
                if len(cells) >= 2:
                    lbl, val = cells[0].get_text(" ", strip=True).lower(), cells[1].get_text(" ", strip=True)
                    num = re.search(r'(\d+(?:\.\d+)?)', val)
                    num = num.group(1) if num else "0"
                    if 'energy' in lbl or 'kcal' in lbl:
                        if 'kcal' in val.lower() or data_map["kcal"]=="0": data_map["kcal"] = num
                    elif 'protein' in lbl: data_map["protein"] = num
                    elif 'carb' in lbl: data_map["carbs"] = num
                    elif 'fat' in lbl: data_map["fat"] = num

        if data_map["kcal"] == "0":
            m = re.search(r'Energy:?\s*(\d+)', container.get_text(" ", strip=True), re.I)
            if m: data_map["kcal"] = m.group(1)

        base_k = float(data_map["kcal"])
        macros = {k: v for k,v in data_map.items() if k!='kcal'}

        servings = [{"label": "100g", "grams": 100}]
        seen = {100}
        if dropdown:
            for opt in dropdown.find_all('option'):
                lbl = opt.get_text(strip=True)
                div = soup.find('div', id=opt.get('value'))
                if div:
                    s_match = re.search(r'(\d+)\s*k?cal', div.get_text(), re.I)
                    if not s_match:
                        tbl = div.find('table')
                        if tbl:
                            for r in tbl.find_all('tr'):
                                if 'energy' in r.text.lower():
                                    mm = re.search(r'(\d+)', r.text)
                                    if mm: s_match = mm; break
                    if s_match and base_k > 0:
                        g = (float(s_match.group(1)) / base_k) * 100
                        final_g = int(g) if g.is_integer() else round(g, 1)
                        if final_g == 100: servings[0]['label'] = lbl
                        elif final_g > 0 and final_g not in seen:
                            seen.add(final_g); servings.append({"label": lbl, "grams": final_g})

        servings.sort(key=lambda x: (1 if x['grams']==100 else 0, x['grams']))
        return {"base": {"kcal": str(int(base_k)), **macros}, "servings": servings}
    except: return None

# --- CALCULATION LOGIC ---
def calculate_needs(weight_kg, height_cm, age, gender, activity, goal):
    if gender == 'male':
        bmr = (10 * weight_kg) + (6.25 * height_cm) - (5 * age) + 5
    else:
        bmr = (10 * weight_kg) + (6.25 * height_cm) - (5 * age) - 161

    multipliers = {'sedentary': 1.2, 'light': 1.375, 'moderate': 1.55, 'active': 1.725}
    tdee = bmr * multipliers.get(activity, 1.2)

    if goal == 'lose': target = tdee - 500
    elif goal == 'gain': target = tdee + 500
    else: target = tdee

    target = int(target)
    if target < 1200: target = 1200 

    p = int((target * 0.30) / 4)
    c = int((target * 0.40) / 4)
    f = int((target * 0.30) / 9)

    return {"kcal": target, "protein": p, "carbs": c, "fat": f}

# --- NEW: RESET ROUTE (FIXES STUCK HOME PAGE) ---
@app.route("/home_reset")
def home_reset():
    # Clear search memory so Dashboard shows up
    session.pop('search_results', None)
    session.pop('selected_item', None)
    session.pop('product_data', None)
    session.pop('final_result', None)
    return redirect(url_for('index'))

@app.route("/", methods=["GET", "POST"])
def index():
    if 'daily_log' not in session: session['daily_log'] = []
    user_goals = session.get('user_goals', {'kcal': 2000, 'protein': 150, 'carbs': 250, 'fat': 70})

    if request.method == "POST":
        action = request.form.get("action")

        if action == "clear_log":
            session['daily_log'] = []
            return redirect(url_for('index'))

        elif action == "remove_item":
            try:
                idx = int(request.form.get("log_index"))
                log = session['daily_log']
                if 0 <= idx < len(log):
                    log.pop(idx)
                    session['daily_log'] = log
            except: pass
            return redirect(url_for('index'))

        # Fixed Back Button Logic
        elif action == "back_to_search":
            return redirect(url_for('home_reset'))

        elif action == "search":
            query = request.form.get("query", "").strip()
            if query:
                try:
                    url = f"https://www.nutracheck.co.uk/CaloriesIn/Product/Search?desc={query.replace(' ', '+')}"
                    r = make_stealth_request(url)
                    soup = BeautifulSoup(r.text, 'html.parser')
                    rows = soup.select('.calsinResultsTable tr')[:8]
                    temp = []
                    for row in rows:
                        a = row.find('a'); img = row.find('img')
                        if a: temp.append({"name": img.get('alt') or a.text, "url": a['href'], "img": img['src'] if img else ''})
                    session['search_results'] = temp
                    session.pop('selected_item', None)
                except: pass
            return redirect(url_for('index'))

        elif action == "select_item":
            try:
                idx = int(request.form.get("item_idx"))
                item = session['search_results'][idx]
                data = get_product_details(item['url'])
                if data:
                    session['selected_item'] = item
                    session['product_data'] = data
                    session['selection_key'] = '0'
                    base = data['base']
                    s = data['servings'][0] if data['servings'] else {'grams':100, 'label':'100g'}
                    fac = s['grams']/100
                    session['final_result'] = {
                        "label": f"1 x {s['label']}", "grams_total": int(s['grams']),
                        "kcal": round(float(base['kcal'])*fac),
                        "protein": round(float(base['protein'])*fac, 1),
                        "carbs": round(float(base['carbs'])*fac, 1),
                        "fat": round(float(base['fat'])*fac, 1),
                        "base_per_100": int(base['kcal']),
                        "base_p": float(base['protein']), "base_c": float(base['carbs']), "base_f": float(base['fat'])
                    }
                    session['current_quantity'] = 1.0
                    session['current_custom_grams'] = ''
            except: pass
            return redirect(url_for('index'))

        elif action == "calculate_preview":
            base = session['product_data']['base']
            key = request.form.get("selection_key")
            session['selection_key'] = key
            if key == "custom":
                g_val = float(request.form.get("custom_grams") or 0)
                session['current_custom_grams'] = g_val
                session['current_quantity'] = 1.0
                fac = g_val/100
                lbl = f"{int(g_val)}g"
                g_tot = int(g_val)
            else:
                s = session['product_data']['servings'][int(key)]
                qty = float(request.form.get("quantity", 1))
                session['current_quantity'] = qty
                fac = (s['grams']/100) * qty
                lbl = f"{qty} x {s['label']}"
                g_tot = int(s['grams'] * qty)
            session['final_result'] = {
                "label": lbl, "grams_total": g_tot,
                "kcal": round(float(base['kcal'])*fac),
                "protein": round(float(base['protein'])*fac, 1),
                "carbs": round(float(base['carbs'])*fac, 1),
                "fat": round(float(base['fat'])*fac, 1),
                "base_per_100": int(base['kcal']),
                "base_p": float(base['protein']), "base_c": float(base['carbs']), "base_f": float(base['fat'])
            }
            return redirect(url_for('index'))

        elif action == "add_smart":
            res = session['final_result']
            item = {
                "name": session['selected_item']['name'],
                "label": res['label'],
                "kcal": res['kcal'],
                "protein": res['protein'],
                "carbs": res['carbs'],
                "fat": res['fat'],
                "meal": request.form.get("meal_target", "Snack")
            }
            session['daily_log'].append(item)
            # Clear search so we go back to dashboard
            session.pop('selected_item', None)
            session.pop('search_results', None)
            return redirect(url_for('index'))

    log = session.get('daily_log', [])
    totals = {
        "kcal": sum(x['kcal'] for x in log),
        "protein": round(sum(x['protein'] for x in log), 1),
        "carbs": round(sum(x['carbs'] for x in log), 1),
        "fat": round(sum(x['fat'] for x in log), 1)
    }
    grouped = {"Breakfast": [], "Lunch": [], "Dinner": [], "Snack": []}
    for i, x in enumerate(log):
        x['original_index'] = i
        grouped[x['meal']].append(x)

    return render_template("index.html",
                           user_goals=user_goals,
                           totals=totals,
                           grouped_log=grouped,
                           results=session.get('search_results'),
                           selected_item=session.get('selected_item'),
                           product_data=session.get('product_data'),
                           final=session.get('final_result'),
                           cur_qty=session.get('current_quantity', 1.0),
                           sel_key=session.get('selection_key', '0'),
                           cur_grams=session.get('current_custom_grams', ''))

@app.route("/profile", methods=["GET", "POST"])
def profile():
    if request.method == "POST":
        logger.info("--- DEBUG: PROFILE BUTTON CLICKED ---")
        try:
            w_raw = request.form.get("weight")
            h_raw = request.form.get("height")
            a_raw = request.form.get("age")
            
            if not w_raw or not h_raw or not a_raw:
                logger.error("ERROR: Missing fields")
                flash("Please fill in Weight, Height and Age.", "error")
                stats = session.get('user_stats', {})
                return render_template("profile.html", stats=stats)

            w = float(w_raw)
            h = float(h_raw)
            a = int(a_raw)
            g = request.form.get("gender")
            act = request.form.get("activity")
            goal = request.form.get("goal")
            
            session['user_stats'] = {"w": w, "h": h, "a": a, "g": g, "act": act, "goal": goal}
            session['user_goals'] = calculate_needs(w, h, a, g, act, goal)
            
            logger.info("DEBUG: Success! Redirecting.")
            flash("Profile Updated!", "success")
            return redirect(url_for('index'))

        except Exception as e:
            logger.error(f"CRITICAL ERROR: {e}")
            flash("Error saving profile.", "error")

    stats = session.get('user_stats', {})
    return render_template("profile.html", stats=stats)

@app.route("/diary")
def diary():
    log = session.get('daily_log', [])
    return render_template("diary.html", log=log)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8500, debug=True)

