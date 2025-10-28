#モジュールをインストールしてください(flask, flask-cors, google-generativeai, python-dotenv)
#実行に成功したら、Running on http://**のリンクから起動できます。
from flask import Flask, request, jsonify, render_template, send_from_directory, abort
from flask_cors import CORS
import os, base64, json, re
import google.generativeai as genai
from dotenv import load_dotenv
import time
import uuid

app = Flask(__name__)
CORS(app)

# ---- Gemini ----
load_dotenv()
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

# データ保存先
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
DATA_FILE = os.path.join(BASE_DIR, "entries.json")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# JSONロード/セーブ
def load_entries():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_entries(entries):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

# ---- ページ ----
@app.route("/")
def index():
    return render_template("oekaki.html")

@app.route("/dex")
def dex():
    return render_template("dex.html")

# ---- API: お絵描き送信 ----
@app.post("/api/upload")
def api_upload():
    try:
        name = (request.form.get("name") or "").strip()
        hint = (request.form.get("hint") or "").strip()
        data_url = request.form.get("imageData")
        if not name or not data_url:
            return jsonify({"error": "name と imageData は必須です"}), 400

        # 画像保存
        if "," in data_url:
            _, b64 = data_url.split(",", 1)
        else:
            b64 = data_url
        img_bytes = base64.b64decode(b64)

        today = time.strftime("%Y-%m-%d")
        save_dir = os.path.join(UPLOAD_DIR, today)
        os.makedirs(save_dir, exist_ok=True)
        fname = f"{uuid.uuid4().hex}.png"
        fpath = os.path.join(save_dir, fname)
        with open(fpath, "wb") as f:
            f.write(img_bytes)

        # ---- Gemini呼び出し ----
        entry_data = {}
        if os.environ.get("GEMINI_API_KEY"):
            model = genai.GenerativeModel("gemini-2.5-flash")
            uploaded = genai.upload_file(fpath)
            prompt = [
                uploaded,
                f"キャラクター名は「{name}」。",
                "次のJSONフォーマットで出力してください。余計な文章は書かず、JSONだけを返してください。",
                """{
                "name": "<キャラクター名(名前は初めに受け取ったものをそのまま出力してください)>",
                "race_job": "<種族や職業>",
                "appearance": "<見た目の特徴>",
                "personality": "<性格>",
                "ability": "<能力>",
                "description": "<全体の図鑑説明>",
                "hint": "<ヒント（もしあれば）>"
                }""",
                'descriptionフィールドは3～5文程度で、性格や日常の様子、能力の活かし方なども盛り込み、図鑑で読んで面白い文章にしてください。'
            ]
            if hint:
                prompt.append(f"ヒント: {hint}")

            raw_text = model.generate_content(prompt).text.strip()
            print("=== Gemini raw output ===")
            print(raw_text)
            print("=========================")

            # --- JSON 部分だけ抽出 ---
            match = re.search(r"\{[\s\S]*\}", raw_text)
            if match:
                try:
                    entry_data = json.loads(match.group(0))
                except json.JSONDecodeError as e:
                    print("JSON decode error:", e)
                    entry_data = {"description": raw_text}
            else:
                entry_data = {"description": raw_text}
        else:
            entry_data = {
                "name": name,
                "race_job": "-",
                "appearance": "-",
                "personality": "-",
                "ability": "-",
                "description": f"{name} の図鑑説明（ダミー）。ヒント: {hint or 'なし'}"
            }

        # ---- 保存 ----
        entries = load_entries()

        # IDを自動連番で付与（削除しても飛び番号OK）
        numeric_ids = [int(e["id"]) for e in entries if str(e.get("id", "")).isdigit()]
        if numeric_ids:
            next_id = max(numeric_ids) + 1
        else:
            next_id = 1


        entry = {
            "id": str(next_id),  # ← 数字ID
            "name": entry_data.get("name", name),
            "hint": hint,
            "race_job": entry_data.get("race_job", "-"),
            "appearance": entry_data.get("appearance", "-"),
            "personality": entry_data.get("personality", "-"),
            "ability": entry_data.get("ability", "-"),
            "description": entry_data.get("description", ""),
            "image_path": f"{today}/{fname}",
            "created_at": int(time.time() * 1000)
        }
        entries.append(entry)
        save_entries(entries)

        return jsonify(entry)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---- API: 図鑑一覧 ----
@app.get("/api/entries")
def list_entries():
    entries = load_entries()
    entries_sorted = sorted(entries, key=lambda e: e["created_at"], reverse=True)
    limit = int(request.args.get("limit", 20))
    return jsonify(entries_sorted[:limit])

# ---- API: 図鑑削除 ----
@app.delete("/api/entries/<entry_id>")
def delete_entry(entry_id):
    entries = load_entries()
    entry = next((e for e in entries if e["id"] == entry_id), None)
    if not entry:
        return jsonify({"error": "Entry not found"}), 404

    # 画像削除
    img_path = os.path.join(UPLOAD_DIR, entry["image_path"])
    if os.path.exists(img_path):
        os.remove(img_path)

    # JSONから削除
    entries = [e for e in entries if e["id"] != entry_id]
    save_entries(entries)

    return jsonify({"success": True, "id": entry_id})

# ---- 画像配信 ----
@app.get("/images/<path:path>")
def serve_image(path):
    base = os.path.abspath(UPLOAD_DIR)
    target = os.path.abspath(os.path.join(base, os.path.dirname(path)))
    if not target.startswith(base):
        abort(403)
    return send_from_directory(base, path)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
