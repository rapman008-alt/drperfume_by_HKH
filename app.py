# app.py
import os
import re
import json
import time
from urllib.parse import quote_plus
from flask import Flask, request, jsonify, send_from_directory
import requests
from bs4 import BeautifulSoup

# Optional: google vision
# from google.cloud import vision

app = Flask(__name__, static_folder='.')

# --------- إعدادات (ضع مفاتيحك هنا إن رغبت) ----------
SERPAPI_KEY = os.getenv('SERPAPI_KEY')    # اختياري: مفيد للبحث الصحيح (serpapi.com)
GOOGLE_VISION_KEY = os.getenv('GOOGLE_VISION_KEY')  # اختياري
# -----------------------------------------------------

# ---------- دوال مساعدة ----------
def safe_text(s):
    return s.strip() if s else ''

def search_query_with_serpapi(query):
    """ مثال استخدام SerpApi (Google Search) — يحتاج API KEY """
    if not SERPAPI_KEY:
        return None
    url = f"https://serpapi.com/search.json?q={quote_plus(query)}&engine=google&google_domain=google.com&gl=us&hl=en&num=5&api_key={SERPAPI_KEY}"
    r = requests.get(url, timeout=12)
    if r.status_code != 200:
        return None
    return r.json()

def scrape_fragrantica_notes(brand_name, perfume_name):
    """محاولة البحث في Fragrantica ثم جلب النوتات من صفحة العطر"""
    base_search = f"https://www.fragrantica.com/search/?q={quote_plus(brand_name + ' ' + perfume_name)}"
    headers = {"User-Agent":"Mozilla/5.0 (compatible)"}
    r = requests.get(base_search, headers=headers, timeout=12)
    if r.status_code != 200:
        return None, [base_search]
    soup = BeautifulSoup(r.text, 'html.parser')

    # محاولة إيجاد أول نتيجة رابط (ممكن تعديل selector حسب تغيّر الموقع)
    link = None
    a = soup.select_one('a[href*="/perfume/"], a[href*="/en/perfume/"]')
    if a and a.get('href'):
        link = a['href']
        if link.startswith('/'):
            link = 'https://www.fragrantica.com' + link

    if not link:
        # حاول أخذ أي رابط بحث يحتوي "perfume"
        for a in soup.find_all('a', href=True):
            if '/perfume/' in a['href']:
                link = a['href']
                if link.startswith('/'):
                    link = 'https://www.fragrantica.com' + link
                break

    if not link:
        return None, [base_search]

    # جلب صفحة العطر
    r2 = requests.get(link, headers=headers, timeout=12)
    if r2.status_code != 200:
        return None, [base_search, link]
    soup2 = BeautifulSoup(r2.text, 'html.parser')

    # حاول إيجاد النوتات — قد تحتاج تعديل selectors لاحقاً
    notes = []
    # تجربة عدة طرق للعثور على النوتات
    # 1) عناصر تحت class يحتوي "notes" أو "accords"
    for sel in ['.notes li', '.note-list li', '.accords li', '.notes__list li']:
        items = soup2.select(sel)
        if items:
            notes = [safe_text(i.get_text()) for i in items if safe_text(i.get_text())]
            break

    # 2) قد تكون في جدول أو نص حر
    if not notes:
        text = soup2.get_text(separator='\n')
        # crude extraction: find "Top notes" etc.
        m = re.search(r'(Top notes|Top Note[s]?|Top :)\s*[:\-]?\s*([^\n\r]+)', text, re.IGNORECASE)
        if m:
            notes = [n.strip() for n in re.split('[,;•]', m.group(2)) if n.strip()]

    return notes if notes else None, [base_search, link]

def scrape_dubaidutyfree_price(perfume_name):
    """محاولة البحث في DubaiDutyFree وابراز السعر/معلومات.
       ملاحظة: تركيب الموقع قد يتطلب استخدام API أو بحث موقع يختلف.
    """
    headers = {"User-Agent":"Mozilla/5.0 (compatible)"}
    search_url = f"https://www.dubaidutyfree.com/search?query={quote_plus(perfume_name)}"
    r = requests.get(search_url, headers=headers, timeout=12)
    if r.status_code != 200:
        return None, [search_url]
    soup = BeautifulSoup(r.text, 'html.parser')

    # محاولات إيجاد أول نتيجة سعر
    # قد يكون في عناصر data-price أو ضمن class يحتوي كلمة price
    price = None
    link = None
    # تجربة: عناصر بها "price" في الكلاس
    price_tag = soup.select_one('.price, .product-price, span[data-price], .price--main')
    if price_tag:
        price = safe_text(price_tag.get_text())
    # الحصول على أول رابط للمنتج
    a = soup.select_one('a.product-card__link, a[href*="/product/"], a[href*="/en/"]')
    if a and a.get('href'):
        link = a['href']
        if link.startswith('/'):
            domain = 'https://www.dubaidutyfree.com'
            link = domain + link

    # إذا وجد رابط، جلب تفاصيل أكثر من الصفحة
    if link:
        r2 = requests.get(link, headers=headers, timeout=12)
        if r2.status_code == 200:
            s2 = BeautifulSoup(r2.text, 'html.parser')
            # دور على عنصر السعر داخل صفحة المنتج
            ptag = s2.select_one('.price, .product-price, span[data-price], .price--main')
            if ptag:
                price = safe_text(ptag.get_text())
    return price, [search_url, link]  # price could be None

# ---------- API route ----------
@app.route('/api/analyze', methods=['POST'])
def api_analyze():
    """
    استقبال form-data:
     - 'photo' (file) اختياري
     - 'name' (text) اختياري (إذا أعطيت اسماً نستخدمه مباشرة)
    """
    name_input = request.form.get('name', '').strip()
    result = {'name': None, 'name_source': None, 'notes': None, 'price': None, 'urls': [], '_debug': ''}

    # 1) إذا أُعطي اسم مباشر استخدمه
    if name_input:
        result['name'] = name_input
        result['name_source'] = 'user_input'
    else:
        # 2) حاول استخلاص اسم من الصورة (إن وُجد)
        photo = request.files.get('photo')
        if not photo:
            return jsonify({'error':'لا صورة ولا اسم معطى'}), 400

        # احفظ مؤقتاً
        tmp_path = os.path.join('/tmp', f'upload_{int(time.time())}.jpg')
        photo.save(tmp_path)

        # إذا أردت تفعيل Google Vision: (مطلوب إعداد credentials)
        # مثال (تعليق افتراضي لأنّ المكتبة قد لا تكون مُعدة):
        if GOOGLE_VISION_KEY:
            # هنا تضع كود استقبال google vision
            # Raise: تحتاج إعداد GOOGLE_APPLICATION_CREDENTIALS في البيئة
            try:
                from google.cloud import vision
                client = vision.ImageAnnotatorClient()
                with open(tmp_path, 'rb') as imgf:
                    content = imgf.read()
                image = vision.Image(content=content)
                resp = client.label_detection(image=image)
                labels = [l.description for l in resp.label_annotations]
                # استخدم labels/ocr لاقتراح اسم
                result['_debug'] += f"vision_labels:{labels}\n"
                # crude: set first label as name candidate
                if labels:
                    result['name'] = labels[0]
                    result['name_source'] = 'google_vision_label'
            except Exception as e:
                result['_debug'] += f"vision_error:{e}\n"

        # إذا لم نحصل اسم بعد، نجرب OCR بسيط أو fallback
        if not result['name']:
            # محاولة اسم الملف أو fallback
            result['_debug'] += 'no_name_from_vision; using filename\n'
            # could set generic name or return ask user to enter name
            result['name_source'] = 'image_fallback'
            result['name'] = os.path.splitext(photo.filename)[0]

    # 3) بحث اسم صحيح (اختياري) — نستخدم SerpApi إن وُجد، وإلا نستخدم الاسم كما هو
    search_debug = {}
    if SERPAPI_KEY:
        try:
            serp = search_query_with_serpapi(result['name'])
            search_debug['serpapi'] = serp.get('organic_results', [])[:3]
            # crude: try to extract a better title
            if serp and serp.get('organic_results'):
                first = serp['organic_results'][0]
                best_title = first.get('title') or first.get('link') or None
                if best_title:
                    result['name'] = best_title
                    result['name_source'] = 'serpapi_top'
                    result['urls'].append(first.get('link'))
        except Exception as e:
            search_debug['serp_error'] = str(e)

    # 4) Fragrantica scraping
    try:
        notes, fr_urls = scrape_fragrantica_notes('', result['name'] or '')
        if notes:
            result['notes'] = notes
        if fr_urls:
            result['urls'].extend(fr_urls)
    except Exception as e:
        result['_debug'] += f"fragrantica_err:{e}\n"

    # 5) DubaiDutyFree price
    try:
        price, dd_urls = scrape_dubaidutyfree_price(result['name'] or '')
        if price:
            result['price'] = price
        if dd_urls:
            result['urls'].extend(dd_urls)
    except Exception as e:
        result['_debug'] += f"dubai_err:{e}\n"

    result['_debug'] += json.dumps(search_debug, default=str)
    return jsonify(result)

# serve static analyze.html if asked
@app.route('/analyze.html')
def send_analyze():
    return send_from_directory('.', 'analyze.html')

if __name__ == '__main__':
    app.run(debug=True, port=5000)
