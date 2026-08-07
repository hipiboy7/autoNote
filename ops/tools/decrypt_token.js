const fs = require('fs');
const path = require('path');
const os = require('os');
const crypto = require('crypto');

const HOME = os.homedir();
const MASTER_KEY_PATH = path.join(HOME, '.gemini', 'config', 'plugins', 'google-workspace', '.gemini-cli-workspace-master-key');
const TOKEN_PATH = path.join(HOME, '.gemini', 'config', 'plugins', 'google-workspace', 'gemini-cli-workspace-token.json');

try {
  const masterKey = fs.readFileSync(MASTER_KEY_PATH);
  const salt = `${os.hostname()}-${os.userInfo().username}-gemini-cli-workspace`;
  const encryptionKey = crypto.scryptSync(masterKey, salt, 32);

  const data = fs.readFileSync(TOKEN_PATH, 'utf-8');
  const parts = data.split(':');
  if (parts.length !== 3) {
    throw new Error('Invalid encrypted data format');
  }

  const iv = Buffer.from(parts[0], 'hex');
  const authTag = Buffer.from(parts[1], 'hex');
  const encrypted = parts[2];

  const decipher = crypto.createDecipheriv('aes-256-gcm', encryptionKey, iv);
  decipher.setAuthTag(authTag);

  let decrypted = decipher.update(encrypted, 'hex', 'utf8');
  decrypted += decipher.final('utf8');

  console.log(decrypted);
} catch (e) {
  console.error("Decryption error: " + e.message);
  process.exit(1);
}
