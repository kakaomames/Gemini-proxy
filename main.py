# api/proxy.py

from flask import Flask, request, Response, send_file
import subprocess
import urllib.parse
import re
import os
import hashlib
import zipfile
import io

from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, urlunparse

app = Flask(__name__)

# /tmp is a writable temporary directory on Vercel
TMP_DIR = "/tmp" 

# HTML contents
INDEX_HTML = """
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ホーム</title>
    <style>
        body { font-family: sans-serif; margin: 2em; background-color: #f4f4f4; color: #333; }
        .container { max-width: 800px; margin: 0 auto; background-color: #fff; padding: 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        h1 { color: #333; border-bottom: 2px solid #eee; padding-bottom: 10px; }
        nav { margin-bottom: 20px; }
        nav a { margin-right: 15px; text-decoration: none; color: #007bff; font-weight: bold; }
        nav a:hover { text-decoration: underline; color: #0056b3; }
        p { line-height: 1.6; }
    </style>
</head>
<body>
    <div class="container">
        <nav>
            <a href="/">ホーム</a>
            <a href="/about">利用規約</a>
            <a href="/proxy">プロキシ</a>
        </nav>
        <h1>ようこそ！</h1>
        <p>このサーバーは、`curl`コマンドを使ってWebコンテンツを取得し表示するシンプルなプロキシです。</p>
        <p>上部のメニューから移動できます。</p>
    </div>
</body>
</html>
"""

TERMS_HTML = """
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>利用規約</title>
    <style>
        body { font-family: sans-serif; margin: 2em; background-color: #f4f4f4; color: #333; }
        .container { max-width: 800px; margin: 0 auto; background-color: #fff; padding: 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        h1 { color: #333; border-bottom: 2px solid #eee; padding-bottom: 10px; }
        nav { margin-bottom: 20px; }
        nav a { margin-right: 15px; text-decoration: none; color: #007bff; font-weight: bold; }
        nav a:hover { text-decoration: underline; color: #0056b3; }
        ul { list-style-type: disc; margin-left: 20px; }
        li { margin-bottom: 10px; }
        p { line-height: 1.6; }
    </style>
</head>
<body>
    <div class="container">
        <nav>
            <a href="/">ホーム</a>
            <a href="/about">利用規約</a>
            <a href="/proxy">プロキシ</a>
        </nav>
        <h1>利用規約</h1>
        <p>本サービス（プロキシ機能を含む）の利用に際し、以下の規約に同意したものとみなします。</p>
        <ul>
            <li>本サービスは学習目的または個人的な利用に限ります。</li>
            <li>違法行為、またはそれに準ずる行為には使用しないでください。</li>
            <li>本サービスの利用によって生じたいかなる損害についても、開発者は一切の責任を負いません。</li>
            <li>予告なくサービス内容を変更または停止する場合があります。</li>
            </ul>
        <p>上記にご同意いただけない場合、本サービスの利用をお控えください。</p>
    </div>
</body>
</html>
"""

PROXY_FORM_HTML = """
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>URLプロキシ</title>
    <style>
        body { font-family: sans-serif; margin: 0; background-color: #f4f4f4; color: #333; }
        .header { background-color: #333; color: white; padding: 10px 2em; display: flex; justify-content: space-between; align-items: center; }
        .header .url-display { font-size: 0.9em; }
        .header .options { font-size: 0.9em; }
        .header a { color: white; text-decoration: none; margin-left: 10px; }
        .header a:hover { text-decoration: underline; }

        .container { max-width: 800px; margin: 2em auto; background-color: #fff; padding: 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        h1 { color: #333; border-bottom: 2px solid #eee; padding-bottom: 10px; }
        nav { margin-bottom: 20px; }
        nav a { margin-right: 15px; text-decoration: none; color: #007bff; font-weight: bold; }
        nav a:hover { text-decoration: underline; color: #0056b3; }
        form { margin-top: 20px; } /* Removed Flexbox for main form */
        form > div { margin-bottom: 10px; display: flex; align-items: center; gap: 10px; }
        form label { min-width: 60px; }
        input[type="text"] { flex-grow: 1; padding: 10px; border: 1px solid #ccc; border-radius: 4px; font-size: 1em; }
        input[type="submit"] { padding: 10px 20px; background-color: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 1em; }
        input[type="submit"]:hover { background-color: #0056b3; }
    </style>
</head>
<body>
    <div class="header">
        <div class="url-display">URL: <span id="current-url"></span></div>
        <div class="options">
            Options:
            <input type="checkbox" id="encrypt-url" name="encrypt-url"> <label for="encrypt-url">Encrypt URL</label>
            <input type="checkbox" id="encrypt-page" name="encrypt-page"> <label for="encrypt-page">Encrypt Page</label>
            <input type="checkbox" id="allow-cookies" name="allow-cookies" checked> <label for="allow-cookies">Allow Cookies</label>
            <input type="checkbox" id="remove-scripts" name="remove-scripts"> <label for="remove-scripts">Remove Scripts</label>
            <input type="checkbox" id="remove-objects" name="remove-objects"> <label for="remove-objects">Remove Objects</label>
            <a href="/">[home]</a> <a href="#">[clear cookies]</a>
        </div>
    </div>

    <div class="container">
        <nav>
            <a href="/">ホーム</a>
            <a href="/about">利用規約</a>
            <a href="/proxy">プロキシ</a>
        </nav>
        <h1>URLプロキシ</h1>
        <p>ここに取得したいURLを入力して「Go」ボタンを押してください。</p>
        <form action="/proxy" method="post">
            <div>
                <label for="url">URL:</label>
                <input type="text" id="url" name="url" placeholder="例: https://www.example.com" value="">
                <input type="submit" value="Go">
            </div>
            <div>
                <input type="checkbox" id="download_zip" name="download_zip">
                <label for="download_zip">ダウンロード (ZIP)</label>
            </div>
        </form>
    </div>
    <script>
        document.getElementById('current-url').textContent = window.location.href;
    </script>
</body>
</html>
"""

# Dictionary to guess Content-Type from file extension (simplified)
MIME_TYPES = {
    '.html': 'text/html', '.css': 'text/css', '.js': 'application/javascript',
    '.json': 'application/json', '.png': 'image/png', '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.svg': 'image/svg+xml',
    '.ico': 'image/x-icon', '.woff': 'font/woff', '.woff2': 'font/woff2',
    '.ttf': 'font/ttf', '.eot': 'application/vnd.ms-fontobject',
    '.zip': 'application/zip',
}

# List of substrings to identify text-based MIME types
TEXT_MIME_SUBSTRINGS = [
    'text/', 'application/json', 'application/javascript', 'application/xml',
    'image/svg+xml' # SVG is XML-based, treat as text
]

# Set to keep track of resource filenames within the ZIP file to avoid duplicates
# This set is reset per request, only for avoiding duplicates within one ZIP
resource_filenames_in_zip = set() 


def is_text_content_type(content_type):
    """
    Determines if a Content-Type indicates text-based content.
    """
    if not content_type:
        return False
    content_type_lower = content_type.lower()
    for sub in TEXT_MIME_SUBSTRINGS:
        if sub in content_type_lower:
            return True
    return False

def _fetch_and_store_binary_resource(resource_url, base_url):
    """
    Fetches a binary URL using curl, saves it to /tmp, and returns its local path,
    Content-Type, actual tmp file path, and a suggested filename for ZIP.
    """
    print(f"Fetching and storing BINARY resource: {resource_url}")
    
    resource_id = hashlib.sha256(resource_url.encode('utf-8')).hexdigest()
    parsed_url = urlparse(resource_url)
    ext = os.path.splitext(parsed_url.path)[1].lower()

    # Try to extract extension from query string if not in path
    if not ext and '?' in parsed_url.query:
        match = re.search(r'\.(png|jpg|jpeg|gif|ico|woff|woff2|ttf|eot)$', parsed_url.query)
        if match:
            ext = '.' + match.group(1)
    
    # Generate a simplified filename for ZIP, handling potential duplicates
    path_segments = [s for s in parsed_url.path.split('/') if s]
    if path_segments:
        suggested_zip_filename = path_segments[-1]
        if not os.path.splitext(suggested_zip_filename)[1]: # If no extension in filename, add from ext variable
             suggested_zip_filename += ext if ext else '.bin' # Default to .bin if no extension found
    else:
        # For root path or domain only, use hostname
        suggested_zip_filename = urlparse(base_url).hostname.replace('.', '_') + ext if ext else '.bin'
    
    original_suggested_zip_filename = suggested_zip_filename
    counter = 0
    while suggested_zip_filename in resource_filenames_in_zip:
        counter += 1
        name, _ext = os.path.splitext(original_suggested_zip_filename)
        suggested_zip_filename = f"{name}_{counter}{_ext}"

    resource_filenames_in_zip.add(suggested_zip_filename)

    tmp_file_path = os.path.join(TMP_DIR, resource_id + ext) # Actual path in /tmp

    try:
        # Get headers first to determine Content-Type
        header_cmd = ['curl', '-sIL', resource_url]
        header_process = subprocess.run(header_cmd, capture_output=True, text=True, check=False, encoding='utf-8', errors='ignore')
        
        content_type = 'application/octet-stream' 
        if header_process.returncode == 0:
            for line in header_process.stdout.splitlines():
                if line.lower().startswith('content-type:'):
                    content_type = line.split(':', 1)[1].strip()
                    break
        else:
            print(f"Warning: Could not get headers for {resource_url}. Curl exit code: {header_process.returncode}, stderr: {header_process.stderr.strip()[:200]}")
            content_type = MIME_TYPES.get(ext, 'application/octet-stream')

        # Fetch the body and save to file
        body_cmd = ['curl', '-sL', '--compressed', '-o', tmp_file_path, resource_url]
        body_process = subprocess.run(body_cmd, capture_output=True, check=True)

        print(f"Binary resource saved to {tmp_file_path} with Content-Type: {content_type}")
        
        return f"/proxy_resource/{resource_id}{ext}", content_type, tmp_file_path, suggested_zip_filename

    except subprocess.CalledProcessError as e:
        print(f"Failed to fetch or save binary resource {resource_url}. Error: {e.stderr}")
        return None, None, None, None
    except Exception as e:
        print(f"Unexpected error for binary resource {resource_url}: {e}")
        return None, None, None, None


def _rewrite_resource_url(base_url, original_url, download_zip_checked, resources_to_zip):
    """
    Rewrites a resource URL based on its content type (text or binary).
    For text, it's proxied via /proxy?url=...
    For binary, it's fetched, stored in /tmp, and proxied via /proxy_resource/<hash>.
    If download_zip_checked is True, all resources are also saved to /tmp for zipping.
    """
    absolute_url = urljoin(base_url, original_url)
    
    # First, get headers to determine Content-Type
    header_cmd = ['curl', '-sIL', absolute_url]
    header_process = subprocess.run(header_cmd, capture_output=True, text=True, check=False, encoding='utf-8', errors='ignore')
    
    fetched_content_type = 'application/octet-stream'
    if header_process.returncode == 0:
        for line in header_process.stdout.splitlines():
            if line.lower().startswith('content-type:'):
                fetched_content_type = line.split(':', 1)[1].strip()
                break
    else:
        print(f"Warning: Could not get headers for {absolute_url}. Using default content type.")

    if is_text_content_type(fetched_content_type):
        # Text-based resources are proxied via /proxy?url=...
        # If ZIP download is checked, also fetch and save the text content to /tmp
        if download_zip_checked:
            try:
                text_content_cmd = ['curl', '-sL', '--compressed', absolute_url]
                text_content_process = subprocess.run(text_content_cmd, capture_output=True, check=True)
                
                # Determine filename for ZIP
                resource_id_for_zip = hashlib.sha256(absolute_url.encode('utf-8')).hexdigest()
                parsed_url = urlparse(absolute_url)
                ext = os.path.splitext(parsed_url.path)[1].lower()
                if not ext and '?' in parsed_url.query:
                    match = re.search(r'\.(js|css|json|txt|svg|html|htm)$', parsed_url.query)
                    if match:
                        ext = '.' + match.group(1)
                suggested_zip_filename = os.path.basename(parsed_url.path) or resource_id_for_zip
                if not os.path.splitext(suggested_zip_filename)[1]:
                    suggested_zip_filename += ext if ext else '.txt'

                original_suggested_zip_filename = suggested_zip_filename
                counter = 0
                while suggested_zip_filename in resource_filenames_in_zip:
                    counter += 1
                    name, _ext = os.path.splitext(original_suggested_zip_filename)
                    suggested_zip_filename = f"{name}_{counter}{_ext}"
                resource_filenames_in_zip.add(suggested_zip_filename)

                tmp_file_path = os.path.join(TMP_DIR, resource_id_for_zip + ext)
                with open(tmp_file_path, 'wb') as f:
                    f.write(text_content_process.stdout)
                
                resources_to_zip.append((tmp_file_path, suggested_zip_filename))
                print(f"Text resource for ZIP saved to {tmp_file_path} as {suggested_zip_filename}")
            except Exception as e:
                print(f"Warning: Failed to fetch text content for ZIP for {absolute_url}: {e}")
        
        # Text resources always rewritten to /proxy?url=... format
        return f"/proxy?url={urllib.parse.quote_plus(absolute_url)}"
    else:
        # Binary resources are handled by _fetch_and_store_binary_resource
        proxied_resource_path, _, tmp_path, zip_filename = _fetch_and_store_binary_resource(absolute_url, base_url)
        if proxied_resource_path:
            if download_zip_checked and tmp_path:
                resources_to_zip.append((tmp_path, zip_filename))
            return proxied_resource_path
        return original_url # If failed, keep original URL

# Helper function to rewrite URLs in CSS 'url()' function (calls _rewrite_resource_url recursively)
def _rewrite_css_url(base_url, css_url, download_zip_checked, resources_to_zip):
    absolute_url = urljoin(base_url, css_url)
    
    # Determine if CSS URL points to text or binary resource
    header_cmd = ['curl', '-sIL', absolute_url]
    header_process = subprocess.run(header_cmd, capture_output=True, text=True, check=False, encoding='utf-8', errors='ignore')
    
    fetched_content_type = 'application/octet-stream'
    if header_process.returncode == 0:
        for line in header_process.stdout.splitlines():
            if line.lower().startswith('content-type:'):
                fetched_content_type = line.split(':', 1)[1].strip()
                break
    else:
        print(f"Warning: Could not get headers for CSS URL {absolute_url}. Using default content type.")

    if is_text_content_type(fetched_content_type):
        # If text-based, proxy via /proxy?url=...
        # If ZIP download, also save to /tmp
        if download_zip_checked:
            try:
                text_content_cmd = ['curl', '-sL', '--compressed', absolute_url]
                text_content_process = subprocess.run(text_content_cmd, capture_output=True, check=True)
                
                resource_id_for_zip = hashlib.sha256(absolute_url.encode('utf-8')).hexdigest()
                parsed_url = urlparse(absolute_url)
                ext = os.path.splitext(parsed_url.path)[1].lower()
                if not ext and '?' in parsed_url.query:
                    match = re.search(r'\.(js|css|json|txt|svg|html|htm)$', parsed_url.query)
                    if match:
                        ext = '.' + match.group(1)
                suggested_zip_filename = os.path.basename(parsed_url.path) or resource_id_for_zip
                if not os.path.splitext(suggested_zip_filename)[1]:
                    suggested_zip_filename += ext if ext else '.txt'

                original_suggested_zip_filename = suggested_zip_filename
                counter = 0
                while suggested_zip_filename in resource_filenames_in_zip:
                    counter += 1
                    name, _ext = os.path.splitext(original_suggested_zip_filename)
                    suggested_zip_filename = f"{name}_{counter}{_ext}"
                resource_filenames_in_zip.add(suggested_zip_filename)

                tmp_file_path = os.path.join(TMP_DIR, resource_id_for_zip + ext)
                with open(tmp_file_path, 'wb') as f:
                    f.write(text_content_process.stdout)
                
                resources_to_zip.append((tmp_file_path, suggested_zip_filename))
                print(f"Text resource (from CSS) for ZIP saved to {tmp_file_path} as {suggested_zip_filename}")
            except Exception as e:
                print(f"Warning: Failed to fetch text content (from CSS) for ZIP for {absolute_url}: {e}")

        return f"/proxy?url={urllib.parse.quote_plus(absolute_url)}"
    else:
        # If binary, handle with _fetch_and_store_binary_resource
        proxied_resource_path, _, tmp_path, zip_filename = _fetch_and_store_binary_resource(absolute_url, base_url)
        if proxied_resource_path:
            if download_zip_checked and tmp_path:
                resources_to_zip.append((tmp_path, zip_filename))
            return proxied_resource_path
        return css_url 


@app.route('/')
def home():
    return INDEX_HTML

@app.route('/about')
def about():
    return TERMS_HTML

# /proxy endpoint handles both:
# 1. Main page requests (POST from form, or GET with URL param)
# 2. Direct proxy requests for text-based resources (GET from rewritten URLs)
@app.route('/proxy', methods=['GET', 'POST'])
def proxy():
    global resource_filenames_in_zip
    resource_filenames_in_zip = set() # Reset for each new main proxy request

    if request.method == 'POST':
        target_url = request.form.get('url', '')
        download_zip_checked = 'download_zip' in request.form
    else: # GET request
        target_url = request.args.get('url', '')
        download_zip_checked = 'download_zip' in request.args

        # If no URL parameter is provided, show the form
        if not target_url:
            return PROXY_FORM_HTML

        # If a URL parameter IS provided in GET request, and it's NOT the initial form submission
        # (i.e., it's a rewritten resource URL like /proxy?url=http://example.com/style.css)
        # Then, serve that resource directly.
        if target_url and 'url' in request.args: 
            try:
                if not (target_url.startswith("http://") or target_url.startswith("https://")):
                    return "Bad Request: URL must start with http:// or https://", 400

                print(f"Direct proxy request for text/script/css: {target_url}")
                
                # Fetch content directly using curl and return
                cmd = ['curl', '-sL', '--compressed', target_url]
                process = subprocess.run(cmd, capture_output=True, check=True)

                # Guess Content-Type from headers
                header_cmd = ['curl', '-sIL', target_url]
                header_process = subprocess.run(header_cmd, capture_output=True, text=True, check=False, encoding='utf-8', errors='ignore')
                
                content_type = 'application/octet-stream' 
                if header_process.returncode == 0:
                    for line in header_process.stdout.splitlines():
                        if line.lower().startswith('content-type:'):
                            content_type = line.split(':', 1)[1].strip()
                            break

                return Response(process.stdout, mimetype=content_type)

            except subprocess.CalledProcessError as e:
                print(f"Curl failed for direct proxy {target_url}: {e.stderr}")
                return "Failed to fetch resource.", 502
            except FileNotFoundError:
                return "Internal Server Error: 'curl' command not found. Please ensure curl is installed and in your PATH.", 500
            except Exception as e:
                print(f"Error for direct proxy {target_url}: {e}")
                return "Internal Server Error.", 500

    # Main HTML page processing logic (triggered by form POST or GET with URL from main form)
    if not (target_url.startswith("http://") or target_url.startswith("https://")):
        return "Bad Request: URL must start with http:// or https://", 400

    print(f"Main proxy request for: {target_url}, Download ZIP: {download_zip_checked}")

    try:
        # Fetch the main HTML content
        html_cmd = ['curl', '-sL', '--compressed', target_url]
        html_process = subprocess.run(html_cmd, capture_output=True, check=True)
        
        main_html_content_bytes = html_process.stdout
        
        # Decode HTML content
        decoded_html_content = None
        for encoding in ['utf-8', 'shift_jis', 'euc_jp', 'latin-1']:
            try:
                decoded_html_content = main_html_content_bytes.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if decoded_html_content is None:
            decoded_html_content = main_html_content_bytes.decode('latin-1', errors='replace')

        soup = BeautifulSoup(decoded_html_content, 'html.parser')
        base_url = target_url 

        # List to store (tmp_file_path, zip_filename) for resources to be included in ZIP
        resources_to_zip = []

        # Add the main HTML itself to resources_to_zip (as 'index.html')
        # We save the *original* main HTML content for ZIP, as rewriting paths inside ZIP is complex.
        # The HTML served to browser will have rewritten URLs.
        main_html_zip_name = "index.html"
        while main_html_zip_name in resource_filenames_in_zip: # Ensure unique name
            main_html_zip_name = f"index_{hashlib.sha256(target_url.encode('utf-8')).hexdigest()[:8]}.html"
        resource_filenames_in_zip.add(main_html_zip_name)
        
        main_html_tmp_path = os.path.join(TMP_DIR, f"main_page_{hashlib.sha256(target_url.encode('utf-8')).hexdigest()}.html")
        with open(main_html_tmp_path, 'wb') as f:
            f.write(main_html_content_bytes) # Save original bytes
        
        if download_zip_checked:
            resources_to_zip.append((main_html_tmp_path, main_html_zip_name))

        # Attributes in HTML tags to rewrite
        attrs_to_rewrite = {
            'a': 'href', 'link': 'href', 'script': 'src', 'img': 'src',
            'form': 'action', 'source': 'src', 'iframe': 'src',
            'audio': 'src', 'video': 'src',
        }

        # Iterate through tags and rewrite URLs
        for tag_name, attr_name in attrs_to_rewrite.items():
            for tag in soup.find_all(tag_name, {attr_name: True}):
                original_url = tag.get(attr_name)
                if original_url and not original_url.startswith('data:') and not original_url.startswith('#'):
                    # Call _rewrite_resource_url to get the appropriate proxied URL
                    proxied_url = _rewrite_resource_url(base_url, original_url, download_zip_checked, resources_to_zip)
                    if proxied_url:
                        tag[attr_name] = proxied_url
                    else:
                        print(f"Warning: Failed to proxy resource: {original_url}")
        
        # Rewrite URLs within <style> tags (CSS url() function)
        def _rewrite_css_url_in_html_style(match):
            original_css_url = match.group(2)
            rewritten_url = _rewrite_css_url(base_url, original_css_url, download_zip_checked, resources_to_zip)
            if rewritten_url:
                return f"url({match.group(1)}{rewritten_url}{match.group(1)})"
            return match.group(0) # If failed, keep original

        for style_tag in soup.find_all('style'):
            if style_tag.string:
                original_css = style_tag.string
                rewritten_css = re.sub(r'url\((["\']?)(.*?)\1\)', _rewrite_css_url_in_html_style, original_css)
                style_tag.string = rewritten_css
                if original_css != rewritten_css:
                    print(f"Rewritten CSS in style tag.")

        # Rewrite <meta http-equiv="refresh"> URLs
        for meta_tag in soup.find_all('meta', {'http-equiv': re.compile(re.escape('refresh'), re.IGNORECASE)}):
            content_attr = meta_tag.get('content')
            if content_attr and 'url=' in content_attr.lower():
                parts = content_attr.split('url=', 1)
                redirect_url = parts[1].strip()
                absolute_redirect_url = urljoin(base_url, redirect_url)
                proxied_redirect_url = f"/proxy?url={urllib.parse.quote_plus(absolute_redirect_url)}"
                meta_tag['content'] = parts[0] + 'url=' + proxied_redirect_url
                print(f"Rewritten meta refresh: {redirect_url} -> {proxied_redirect_url}")


        # Get the final rewritten HTML bytes for response
        response_content_bytes = str(soup).encode('utf-8')
        content_type = 'text/html; charset=utf-8'

        # If ZIP download option is checked
        if download_zip_checked:
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                # Add the rewritten HTML as 'proxied_index.html' to the ZIP
                zf.writestr('proxied_index.html', response_content_bytes) 
                print(f"Added to ZIP: rewritten HTML as proxied_index.html")

                # Add other resources from the collected list
                for tmp_file_path, zip_filename in resources_to_zip:
                    # Skip the main HTML if it was already added as 'index.html' (original)
                    if os.path.exists(tmp_file_path):
                        zf.write(tmp_file_path, arcname=zip_filename)
                        print(f"Added to ZIP: {tmp_file_path} as {zip_filename}")
            
            zip_buffer.seek(0) # Rewind buffer to the beginning

            # Provide the ZIP file for download
            return send_file(
                zip_buffer,
                mimetype='application/zip',
                as_attachment=True,
                download_name=f"{urlparse(target_url).hostname.replace('.', '_')}.zip"
            )
        else:
            # Otherwise, serve the rewritten HTML page
            return Response(response_content_bytes, mimetype=content_type)

    except subprocess.CalledProcessError as e:
        error_message = f"Curl failed to fetch URL. Status: {e.returncode}"
        if e.stderr:
            error_message += f"\nError Output: {e.stderr.strip()[:500]}"
        return error_message, 502
    except FileNotFoundError:
        return "Internal Server Error: 'curl' command not found. Please ensure curl is installed and in your PATH.", 500
    except Exception as e:
        print(f"General error for {target_url}: {e}")
        return f"Internal Server Error: {e}", 500

@app.route('/proxy_resource/<path:resource_id_ext>')
def serve_tmp_resource(resource_id_ext):
    """
    Serves binary resources (e.g., images) from /tmp based on the proxied URL.
    """
    tmp_file_path = os.path.join(TMP_DIR, resource_id_ext)
    
    if not os.path.exists(tmp_file_path):
        # If resource not found in /tmp, return 404.
        print(f"Resource not found in temporary storage: {tmp_file_path}. Returning 404.")
        return "Resource not found.", 404
    
    ext = os.path.splitext(resource_id_ext)[1].lower()
    content_type = MIME_TYPES.get(ext, 'application/octet-stream')

    try:
        return send_file(tmp_file_path, mimetype=content_type)

    except Exception as e:
        print(f"Error serving resource {tmp_file_path}: {e}")
        return f"Error serving resource: {e}", 500

# Entry point for Vercel (and local development)
if __name__ == '__main__':
    # Create /tmp directory if it doesn't exist (for local testing)
    if not os.path.exists(TMP_DIR):
        os.makedirs(TMP_DIR)
        print(f"Created {TMP_DIR} directory for local testing.")
    app.run(debug=True, port=8080)

