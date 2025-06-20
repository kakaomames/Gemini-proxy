# api/proxy.py
from flask import Flask, request, Response, send_file
import subprocess
import urllib.parse
import re
import os
import hashlib
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, urlunparse
import io # send_fileのためにバイトデータを扱う

app = Flask(__name__)

TMP_DIR = "/tmp"
# api/proxy.py (抜粋 - ファイルの先頭近くに配置)

# 簡易的なコンテンツ
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
        form { display: flex; align-items: center; gap: 10px; margin-top: 20px; }
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
            <label for="url">URL:</label>
            <input type="text" id="url" name="url" placeholder="例: https://www.example.com" value="">
            <input type="submit" value="Go">
        </form>
    </div>
    <script>
        // 現在のURLをヘッダーに表示 (これはブラウザのURLなので、プロキシが取得したURLとは異なります)
        document.getElementById('current-url').textContent = window.location.href;
    </script>
</body>
</html>
"""

# ... (ここからFlaskアプリの定義や関数などが続きます)
# app = Flask(__name__)
# TMP_DIR = "/tmp"
# ...

# MIME_TYPES 辞書は同じ
MIME_TYPES = {
    '.html': 'text/html', '.css': 'text/css', '.js': 'application/javascript',
    '.json': 'application/json', '.png': 'image/png', '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.svg': 'image/svg+xml',
    '.ico': 'image/x-icon', '.woff': 'font/woff', '.woff2': 'font/woff2',
    '.ttf': 'font/ttf', '.eot': 'application/vnd.ms-fontobject',
}


def _fetch_and_store_resource(resource_url):
    """
    URLをcurlで取得し、/tmpに保存して、保存先のパスとContent-Typeを返す。
    """
    print(f"Fetching and storing resource: {resource_url}")
    
    resource_id = hashlib.sha256(resource_url.encode('utf-8')).hexdigest()
    parsed_url = urlparse(resource_url)
    ext = os.path.splitext(parsed_url.path)[1].lower()
    
    # 拡張子がない場合でも対応できるように調整
    if not ext and '?' in parsed_url.query: # クエリに拡張子情報が含まれる場合 (例: /script?v=1.2.3.js)
        match = re.search(r'\.(js|css|png|jpg|gif|svg)$', parsed_url.query)
        if match:
            ext = '.' + match.group(1)

    tmp_file_path = os.path.join(TMP_DIR, resource_id + ext)

    try:
        # --- まずContent-Typeを取得するためにヘッダーのみを取得 ---
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

        # --- 次にコンテンツボディを取得し、ファイルに保存 ---
        body_cmd = ['curl', '-sL', '--compressed', '-o', tmp_file_path, resource_url]
        body_process = subprocess.run(body_cmd, capture_output=True, check=True)

        print(f"Resource saved to {tmp_file_path} with Content-Type: {content_type}")
        return f"/proxy_resource/{resource_id}{ext}", content_type

    except subprocess.CalledProcessError as e:
        print(f"Failed to fetch or save resource {resource_url}. Error: {e.stderr}")
        return None, None
    except Exception as e:
        print(f"Unexpected error for resource {resource_url}: {e}")
        return None, None

@app.route('/')
def home():
    return INDEX_HTML

@app.route('/about')
def about():
    return TERMS_HTML

@app.route('/proxy', methods=['GET', 'POST'])
def proxy():
    if request.method == 'POST':
        target_url = request.form.get('url', '')
    else: # GET
        target_url = request.args.get('url', '')

    if not target_url:
        return PROXY_FORM_HTML, 400

    if not (target_url.startswith("http://") or target_url.startswith("https://")):
        return "Bad Request: URL must start with http:// or https://", 400

    print(f"Proxy request for: {target_url}")

    try:
        html_cmd = ['curl', '-sL', '--compressed', target_url]
        html_process = subprocess.run(html_cmd, capture_output=True, check=True)
        
        content_type = 'text/html; charset=utf-8' # デフォルト
        response_content_bytes = html_process.stdout

        if 'text/html' in content_type:
            decoded_html_content = None
            for encoding in ['utf-8', 'shift_jis', 'euc_jp', 'latin-1']:
                try:
                    decoded_html_content = response_content_bytes.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            
            if decoded_html_content is None:
                decoded_html_content = response_content_bytes.decode('latin-1', errors='replace')

            soup = BeautifulSoup(decoded_html_content, 'html.parser')
            base_url = target_url 

            attrs_to_rewrite = {
                'a': 'href', 'link': 'href', 'script': 'src', 'img': 'src',
                'form': 'action', 'source': 'src', 'iframe': 'src',
                'audio': 'src', 'video': 'src',
            }

            for tag_name, attr_name in attrs_to_rewrite.items():
                for tag in soup.find_all(tag_name, {attr_name: True}):
                    original_url = tag.get(attr_name)
                    if original_url and not original_url.startswith('data:') and not original_url.startswith('#'): # data: URIとフラグメントURIは無視
                        absolute_url = urljoin(base_url, original_url)
                        proxied_resource_path, _ = _fetch_and_store_resource(absolute_url)
                        
                        if proxied_resource_path:
                            tag[attr_name] = proxied_resource_path
                        else:
                            print(f"Warning: Failed to proxy resource: {absolute_url}")
            
            # CSS内のurl()関数を書き換える簡易ロジック
            # これは非常に限定的であり、正確なCSSパースは行わない
            for style_tag in soup.find_all('style'):
                if style_tag.string:
                    original_css = style_tag.string
                    # url(...) パターンを検索
                    rewritten_css = re.sub(r'url\((["\']?)(.*?)\1\)', 
                                           lambda m: f"url({m.group(1)}{_rewrite_css_url(base_url, m.group(2))}{m.group(1)})", 
                                           original_css)
                    style_tag.string = rewritten_css
                    if original_css != rewritten_css:
                        print(f"Rewritten CSS in style tag.")

            # <meta http-equiv="refresh"> の content を書き換え
            for meta_tag in soup.find_all('meta', {'http-equiv': re.compile(re.escape('refresh'), re.IGNORECASE)}):
                content_attr = meta_tag.get('content')
                if content_attr and 'url=' in content_attr.lower():
                    parts = content_attr.split('url=', 1)
                    redirect_url = parts[1].strip()
                    absolute_redirect_url = urljoin(base_url, redirect_url)
                    proxied_redirect_url = f"/proxy?url={urllib.parse.quote_plus(absolute_redirect_url)}"
                    meta_tag['content'] = parts[0] + 'url=' + proxied_redirect_url
                    print(f"Rewritten meta refresh: {redirect_url} -> {proxied_redirect_url}")


            response_content_bytes = str(soup).encode('utf-8')
            if 'charset=' not in content_type:
                content_type = 'text/html; charset=utf-8'

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
    /proxy_resource/<resource_id> のリクエストを処理し、/tmpからファイルを読み込んで返す。
    """
    tmp_file_path = os.path.join(TMP_DIR, resource_id_ext)
    
    if not os.path.exists(tmp_file_path):
        return "Resource not found in temporary storage.", 404
    
    ext = os.path.splitext(resource_id_ext)[1].lower()
    content_type = MIME_TYPES.get(ext, 'application/octet-stream')

    try:
        return send_file(tmp_file_path, mimetype=content_type)

    except Exception as e:
        print(f"Error serving resource {tmp_file_path}: {e}")
        return f"Error serving resource: {e}", 500

# CSS内のurl()を書き換えるヘルパー関数
def _rewrite_css_url(base_url, css_url):
    absolute_url = urljoin(base_url, css_url)
    proxied_resource_path, _ = _fetch_and_store_resource(absolute_url)
    if proxied_resource_path:
        return proxied_resource_path
    return css_url # 失敗したら元のURLのまま

# Vercelが呼び出すアプリケーションのエントリポイント
if __name__ == '__main__':
    # ローカルでのテスト用
    if not os.path.exists(TMP_DIR):
        os.makedirs(TMP_DIR)
        print(f"Created {TMP_DIR} directory for local testing.")
    app.run(debug=True, port=PORT)
