import os
import json
import sys
import subprocess
import urllib.request
import urllib.parse
import urllib.error

PARENT_FOLDER_ID = "1mHmyPezUsD4NF5AlIbHkzKzf6TymmesH" # 투자리서치노트_gemini

def get_fresh_access_token():
    try:
        decrypt_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'decrypt_token.js')
        res = subprocess.run(["node", decrypt_script], capture_output=True, text=True, check=True)
        tokens = json.loads(res.stdout.strip())
        return tokens.get("main-account", {}).get("token", {}).get("accessToken")
    except Exception as e:
        print(f"Error fetching decrypted token: {e}", file=sys.stderr)
        return None

def create_google_doc(title, access_token):
    url = "https://docs.googleapis.com/v1/documents"
    body = {"title": title}
    body_bytes = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=body_bytes,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req) as res:
            doc_data = json.loads(res.read().decode('utf-8'))
            return doc_data.get("documentId")
    except Exception as e:
        print(f"Error creating doc: {e}", file=sys.stderr)
        return None

def move_file_to_folder(file_id, folder_id, access_token):
    get_url = f"https://www.googleapis.com/drive/v3/files/{file_id}?fields=parents"
    req = urllib.request.Request(get_url, headers={"Authorization": f"Bearer {access_token}"})
    try:
        with urllib.request.urlopen(req) as res:
            file_data = json.loads(res.read().decode('utf-8'))
            parents = file_data.get("parents", [])
    except Exception as e:
        print(f"Error getting file parents: {e}", file=sys.stderr)
        parents = []
        
    add_parents = folder_id
    remove_parents = ",".join(parents) if parents else ""
    
    update_url = f"https://www.googleapis.com/drive/v3/files/{file_id}?addParents={add_parents}"
    if remove_parents:
        update_url += f"&removeParents={remove_parents}"
        
    req = urllib.request.Request(
        update_url,
        headers={"Authorization": f"Bearer {access_token}"},
        method="PATCH"
    )
    try:
        with urllib.request.urlopen(req) as res:
            print(f"Moved file {file_id} to folder {folder_id}", file=sys.stderr)
            return True
    except Exception as e:
        print(f"Error moving file: {e}", file=sys.stderr)
        return False

def append_text_to_doc(doc_id, text_to_append, access_token):
    get_url = f"https://docs.googleapis.com/v1/documents/{doc_id}"
    req = urllib.request.Request(get_url, headers={"Authorization": f"Bearer {access_token}"})
    try:
        with urllib.request.urlopen(req) as res:
            doc_data = json.loads(res.read().decode('utf-8'))
            body = doc_data.get("body", {})
            end_index = body.get("content", [])[-1].get("endIndex", 1)
    except Exception as e:
        print(f"Error fetching doc {doc_id}: {e}", file=sys.stderr)
        return False
        
    target_index = max(1, end_index - 1)
    requests = [{
        "insertText": {
            "location": {
                "index": target_index
            },
            "text": text_to_append
        }
    }]
    
    update_url = f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate"
    body_data = json.dumps({"requests": requests}).encode('utf-8')
    update_req = urllib.request.Request(
        update_url, 
        data=body_data, 
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
    )
    try:
        with urllib.request.urlopen(update_req) as res:
            return True
    except Exception as e:
        print(f"Error writing header: {e}", file=sys.stderr)
        return False

def main():
    if len(sys.argv) < 3:
        print("Usage: python create_doc.py <title> <header_text>", file=sys.stderr)
        sys.exit(1)
        
    title = sys.argv[1]
    header_text = sys.argv[2]
    
    access_token = get_fresh_access_token()
    if not access_token:
        print("Failed to get access token", file=sys.stderr)
        sys.exit(1)
        
    doc_id = create_google_doc(title, access_token)
    if not doc_id:
        print("Failed to create doc", file=sys.stderr)
        sys.exit(1)
        
    move_file_to_folder(doc_id, PARENT_FOLDER_ID, access_token)
    append_text_to_doc(doc_id, header_text, access_token)
    
    print(doc_id)

if __name__ == '__main__':
    main()
