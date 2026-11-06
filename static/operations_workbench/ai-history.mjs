// Operations AI conversations, kept in this browser's IndexedDB and scoped to the signed-in account.
const NAME = 'shdr-operations-ai';
const STORE = 'sessions';
const LIMIT = 50;
let opening;

function database() {
  opening ||= new Promise((resolve, reject) => {
    const request = indexedDB.open(NAME, 1);
    request.onupgradeneeded = () => {
      request.result.createObjectStore(STORE, {keyPath: 'id'}).createIndex('account', 'account');
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  opening.catch(() => { opening = null; });
  return opening;
}

async function run(mode, operation) {
  const db = await database();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction(STORE, mode);
    const request = operation(transaction.objectStore(STORE));
    transaction.oncomplete = () => resolve(request?.result);
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
}

export async function listSessions(account) {
  const rows = await run('readonly', store => store.index('account').getAll(account));
  return (rows || []).sort((a, b) => String(b.updated).localeCompare(String(a.updated)));
}

export async function saveSession(session) {
  await run('readwrite', store => store.put(session));
  const rows = await listSessions(session.account);
  for (const old of rows.slice(LIMIT)) await deleteSession(old.id);
}

export function deleteSession(id) {
  return run('readwrite', store => store.delete(id));
}

export async function clearSessions(account) {
  for (const row of await listSessions(account)) await deleteSession(row.id);
}
