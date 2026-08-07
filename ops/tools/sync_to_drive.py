import os
import json
import hashlib
import subprocess
import urllib.request
import urllib.parse
import urllib.error
import mimetypes
import sys

# Windows 콘솔 인코딩 에러 방지 (한글 출력 지원)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

PARENT_FOLDER_ID = "1mHmyPezUsD4NF5AlIbHkzKzf6TymmesH"  # 투자리서치노트_gemini
LOCAL_DIR = "D:\\autoNote"

# 제외할 폴더 및 파일 패턴
EXCLUDE_DIRS = {
    ".git", ".idea", "__pycache__", "node_modules", ".gemini", ".claude",
    "venv", ".venv", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    "telegram-mcp",  # Telethon 세션 및 API 자격증명 보관 위치 — 업로드 절대 금지
}
# state.json 등은 로컬 런타임 전용이므로 업로드 제외
EXCLUDE_FILES = {
    ".gitignore", "desktop.ini",
    "state.json", "state.json.bak", "batch.lock",
}

# 자격증명 유출 방지용 이름 규칙 (AGENTS.md 절대 규칙: 세션 파일·API 자격증명 전송 금지)
# 신규 시크릿 파일이 추가되어도 목록 갱신 없이 자동 차단되도록 접두/접미 규칙으로 방어한다.
EXCLUDE_FILE_PREFIXES = (".env",)
EXCLUDE_FILE_SUFFIXES = (
    ".session", ".session-journal",  # Telethon
    ".pem", ".key", ".p12", ".pfx",  # 인증서 / 개인키
    ".credentials", ".token",
)


def is_excluded_file(file_name: str) -> bool:
    """업로드 대상에서 제외해야 하는 파일인지 판정한다."""
    if file_name in EXCLUDE_FILES:
        return True
    lowered = file_name.lower()
    if lowered.startswith(EXCLUDE_FILE_PREFIXES):
        return True
    if lowered.endswith(EXCLUDE_FILE_SUFFIXES):
        return True
    return False

def get_fresh_access_token():
    try:
        decrypt_script = os.path.join("D:\\autoNote", "scripts", "decrypt_token.js")
        res = subprocess.run(["node", decrypt_script], capture_output=True, text=True, check=True)
        tokens = json.loads(res.stdout.strip())
        return tokens.get("main-account", {}).get("token", {}).get("accessToken")
    except Exception as e:
        print(f"Error fetching decrypted token: {e}")
        return None

def get_md5(file_path):
    hasher = hashlib.md5()
    try:
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception as e:
        print(f"Error calculating MD5 for {file_path}: {e}")
        return ""

def drive_api_request(url, method="GET", headers=None, data=None):
    if headers is None:
        headers = {}
    req = urllib.request.Request(url, headers=headers, method=method)
    if data is not None:
        req.data = data
    try:
        with urllib.request.urlopen(req) as res:
            return json.loads(res.read().decode('utf-8'))
    except urllib.error.HTTPError as he:
        err_msg = he.read().decode('utf-8', errors='ignore')
        print(f"Drive API HTTP Error ({he.code}) for {url}: {err_msg}")
        raise he
    except Exception as e:
        print(f"Drive API Connection Error: {e}")
        raise e

def find_or_create_folder(folder_name, parent_id, access_token):
    # parent_id 내에 folder_name을 가진 폴더 검색
    query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and '{parent_id}' in parents and trashed = false"
    url = f"https://www.googleapis.com/drive/v3/files?q={urllib.parse.quote(query)}&fields=files(id)"
    headers = {"Authorization": f"Bearer {access_token}"}
    
    result = drive_api_request(url, headers=headers)
    files = result.get("files", [])
    if files:
        return files[0]["id"]
    
    # 폴더가 없으면 새로 생성
    create_url = "https://www.googleapis.com/drive/v3/files"
    body = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id]
    }
    body_bytes = json.dumps(body).encode('utf-8')
    headers.update({"Content-Type": "application/json"})
    
    res = drive_api_request(create_url, method="POST", headers=headers, data=body_bytes)
    print(f"Created remote folder: {folder_name} (ID: {res['id']})")
    return res["id"]

def get_drive_files_in_folder(folder_id, access_token):
    # 특정 폴더 안의 파일 및 폴더 목록 조회 (MD5 체크섬 포함)
    query = f"'{folder_id}' in parents and trashed = false"
    url = f"https://www.googleapis.com/drive/v3/files?q={urllib.parse.quote(query)}&fields=files(id,name,mimeType,md5Checksum)&pageSize=1000"
    headers = {"Authorization": f"Bearer {access_token}"}
    
    result = drive_api_request(url, headers=headers)
    return result.get("files", [])

def upload_new_file(file_path, file_name, parent_id, access_token):
    # Multipart upload로 신규 파일 업로드
    boundary = b"314159265358979323846"
    mime_type, _ = mimetypes.guess_type(file_path)
    if mime_type is None:
        mime_type = "application/octet-stream"
        
    metadata = {
        "name": file_name,
        "parents": [parent_id]
    }
    
    with open(file_path, "rb") as f:
        file_content = f.read()
        
    # Multipart body 구성
    body = (
        b"--" + boundary + b"\r\n" +
        b"Content-Type: application/json; charset=UTF-8\r\n\r\n" +
        json.dumps(metadata).encode('utf-8') + b"\r\n" +
        b"--" + boundary + b"\r\n" +
        b"Content-Type: " + mime_type.encode('utf-8') + b"\r\n\r\n" +
        file_content + b"\r\n" +
        b"--" + boundary + b"--\r\n"
    )
    
    url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": b"multipart/related; boundary=" + boundary,
        "Content-Length": str(len(body))
    }
    
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as res:
            res_json = json.loads(res.read().decode('utf-8'))
            print(f"Uploaded new file: {file_name} (ID: {res_json['id']})")
            return res_json["id"]
    except Exception as e:
        print(f"Error uploading {file_name}: {e}")
        return None

def update_existing_file(file_path, file_id, file_name, access_token):
    # Media upload로 기존 파일 내용 덮어쓰기
    with open(file_path, "rb") as f:
        file_content = f.read()
        
    url = f"https://www.googleapis.com/upload/drive/v3/files/{file_id}?uploadType=media"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/octet-stream",
        "Content-Length": str(len(file_content))
    }
    
    req = urllib.request.Request(url, data=file_content, headers=headers, method="PATCH")
    try:
        with urllib.request.urlopen(req) as res:
            res_json = json.loads(res.read().decode('utf-8'))
            print(f"Updated existing file: {file_name} (ID: {file_id})")
            return file_id
    except Exception as e:
        print(f"Error updating {file_name}: {e}")
        return None

def sync_directory(local_path, remote_folder_id, access_token):
    # 로컬 경로 탐색
    print(f"Scanning local directory: {local_path}")
    
    # 1. 원격 폴더 내 기존 파일 목록 가져오기
    drive_items = get_drive_files_in_folder(remote_folder_id, access_token)
    drive_files = {item["name"]: item for item in drive_items if item["mimeType"] != "application/vnd.google-apps.folder"}
    drive_folders = {item["name"]: item for item in drive_items if item["mimeType"] == "application/vnd.google-apps.folder"}
    
    local_items = os.listdir(local_path)
    
    for item in local_items:
        full_local_path = os.path.join(local_path, item)
        
        # 제외 대상 필터링
        if os.path.isdir(full_local_path):
            if item in EXCLUDE_DIRS:
                continue
            # 원격에 매칭되는 폴더 생성/조회
            sub_folder_id = find_or_create_folder(item, remote_folder_id, access_token)
            # 재귀적으로 서브디렉토리 동기화 진행
            sync_directory(full_local_path, sub_folder_id, access_token)
            
        elif os.path.isfile(full_local_path):
            if is_excluded_file(item):
                print(f"Skipped (excluded): {item}")
                continue

            local_md5 = get_md5(full_local_path)
            
            # 파일이 이미 원격에 존재하는지 검사
            if item in drive_files:
                drive_file = drive_files[item]
                remote_md5 = drive_file.get("md5Checksum", "")
                
                # MD5가 다르면 업데이트 진행
                if local_md5 != remote_md5:
                    print(f"File modified (MD5 mismatch). Updating: {item}")
                    update_existing_file(full_local_path, drive_file["id"], item, access_token)
                else:
                    # 동일한 파일은 업로드 스킵
                    pass
            else:
                # 원격에 없는 파일은 신규 업로드
                print(f"New file found. Uploading: {item}")
                upload_new_file(full_local_path, item, remote_folder_id, access_token)

def main():
    print("Initializing autoNote Google Drive Sync...")
    access_token = get_fresh_access_token()
    if not access_token:
        print("Failed to get Google Access Token.")
        return
        
    print(f"Target Parent Folder ID: {PARENT_FOLDER_ID}")
    
    # 1. '투자리서치노트_gemini' 아래에 'autoNote' 폴더 생성/조회
    root_folder_id = find_or_create_folder("autoNote", PARENT_FOLDER_ID, access_token)
    print(f"Syncing with root autoNote Folder ID: {root_folder_id}")
    
    # 2. 동기화 실행
    sync_directory(LOCAL_DIR, root_folder_id, access_token)
    print("\nGoogle Drive Sync completed successfully.")

if __name__ == "__main__":
    main()
