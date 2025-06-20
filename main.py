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
