import os, io, base64, requests, tempfile
from flask import Flask, request, render_template, send_from_directory, redirect, url_for, flash
from werkzeug.utils import secure_filename
from pdf2image import convert_from_path
from PIL import Image
import pytesseract
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import matplotlib.pyplot as plt

UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'outputs'
ALLOWED_EXTENSIONS = {'pdf'}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET', 'change-me-in-production')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER

MATHPIX_APP_ID = os.environ.get('MATHPIX_APP_ID')
MATHPIX_APP_KEY = os.environ.get('MATHPIX_APP_KEY')
MATHPIX_URL = 'https://api.mathpix.com/v3/text'

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/', methods=['GET'])
def index():
    return render_template('index.html')

def call_mathpix_image_b64(image_b64):
    if not MATHPIX_APP_ID or not MATHPIX_APP_KEY:
        return None
    headers = {
        'app_id': MATHPIX_APP_ID,
        'app_key': MATHPIX_APP_KEY,
        'Content-type': 'application/json'
    }
    data = {
        'src': 'data:image/png;base64,' + image_b64,
        'formats': ['latex_simplified'],
        'ocr': ['math','text']
    }
    try:
        r = requests.post(MATHPIX_URL, headers=headers, json=data, timeout=30)
        if r.status_code == 200:
            return r.json()
        else:
            app.logger.error('Mathpix error %s %s', r.status_code, r.text)
            return None
    except Exception as e:
        app.logger.error('Mathpix call failed: %s', e)
        return None

def render_latex_to_png(latex, dpi=200):
    fig = plt.figure(figsize=(0.01,0.01))
    fig.text(0, 0, r'$%s$' % latex, fontsize=18)
    buf = io.BytesIO()
    try:
        plt.axis('off')
        plt.savefig(buf, format='png', dpi=dpi, bbox_inches='tight', pad_inches=0.1)
        plt.close(fig)
        buf.seek(0)
        return Image.open(buf).convert('RGBA')
    except Exception as e:
        plt.close(fig)
        app.logger.error('LaTeX render error: %s', e)
        return None

@app.route('/upload', methods=['POST'])
def upload():
    if 'pdf' not in request.files:
        flash('No PDF file part')
        return redirect(url_for('index'))
    pdf_file = request.files['pdf']
    if pdf_file.filename == '':
        flash('No selected file')
        return redirect(url_for('index'))
    if not allowed_file(pdf_file.filename):
        flash('Invalid file type')
        return redirect(url_for('index'))
    pdf_filename = secure_filename(pdf_file.filename)
    pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], pdf_filename)
    pdf_file.save(pdf_path)

    font_path = None
    if 'font' in request.files and request.files['font'].filename != '':
        font_file = request.files['font']
        font_filename = secure_filename(font_file.filename)
        font_path = os.path.join(app.config['UPLOAD_FOLDER'], font_filename)
        font_file.save(font_path)

    try:
        pages = convert_from_path(pdf_path, dpi=300)
    except Exception as e:
        flash(f'Error converting PDF to images: {e}')
        return redirect(url_for('index'))

    extracted_texts = []
    page_images = []
    math_images = []

    for i, page in enumerate(pages):
        img_path = os.path.join(app.config['UPLOAD_FOLDER'], f"page_{i+1}.png")
        page.save(img_path, 'PNG')
        page_images.append(img_path)

        text = pytesseract.image_to_string(Image.open(img_path))
        extracted_texts.append(text)

        with open(img_path, 'rb') as f:
            img_b64 = base64.b64encode(f.read()).decode('utf-8')
        mp_res = call_mathpix_image_b64(img_b64)
        latex_png = None
        if mp_res and mp_res.get('latex_simplified'):
            latex = mp_res.get('latex_simplified')
            latex_png = render_latex_to_png(latex)
        math_images.append(latex_png)

    out_name = pdf_filename.rsplit('.',1)[0] + '_converted.pdf'
    out_path = os.path.join(app.config['OUTPUT_FOLDER'], out_name)
    c = canvas.Canvas(out_path, pagesize=A4)
    width, height = A4

    if font_path:
        try:
            pdfmetrics.registerFont(TTFont('UserHand', font_path))
            chosen_font = 'UserHand'
        except Exception as e:
            app.logger.error('Font register error: %s', e)
            chosen_font = 'Helvetica'
    else:
        chosen_font = 'Helvetica'

    for i, img_path in enumerate(page_images):
        c.drawImage(img_path, 0, 0, width=width, height=height)
        if math_images[i] is not None:
            tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
            math_images[i].save(tmp.name)
            img_w, img_h = math_images[i].size
            max_w = width * 0.45
            scale = min(1.0, max_w / img_w)
            draw_w = img_w * scale
            draw_h = img_h * scale
            c.drawImage(tmp.name, width - draw_w - 30, height - draw_h - 60, width=draw_w, height=draw_h, mask='auto')

        c.setFont(chosen_font, 12)
        text_obj = c.beginText(30, height - 40)
        for line in extracted_texts[i].splitlines():
            text_obj.textLine(line[:200])
        c.drawText(text_obj)
        c.showPage()

    c.save()
    flash('Conversion finished. Download below.')
    return redirect(url_for('download_file', filename=out_name))

@app.route('/outputs/<path:filename>', methods=['GET'])
def download_file(filename):
    return send_from_directory(app.config['OUTPUT_FOLDER'], filename, as_attachment=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)), debug=True)
