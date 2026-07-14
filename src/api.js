/* Fetch wrapper — base URL from VITE_API_URL, JWT from memory, auto-refresh on 401 (one
   retry), JSON + multipart helpers, typed error surface. Refresh token lives in
   localStorage (static hosting — no httpOnly cookies available; see production/SECURITY.md). */

const BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";
const REFRESH_KEY = "baton.refresh_token";

let accessToken = null; // memory only

export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === "string" ? detail : detail?.reason || detail?.message || JSON.stringify(detail));
    this.status = status;
    this.detail = detail;
  }
}

export const setTokens = ({ access_token, refresh_token }) => {
  accessToken = access_token || null;
  if (refresh_token) localStorage.setItem(REFRESH_KEY, refresh_token);
};
export const clearTokens = () => {
  accessToken = null;
  localStorage.removeItem(REFRESH_KEY);
};
export const getRefreshToken = () => localStorage.getItem(REFRESH_KEY);
export const decodeJwt = (token) => {
  try {
    return JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
  } catch {
    return null;
  }
};
export const currentUserId = () => (accessToken ? decodeJwt(accessToken)?.sub : null);

async function refreshOnce() {
  const rt = getRefreshToken();
  if (!rt) return false;
  const r = await fetch(`${BASE}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: rt }),
  });
  if (!r.ok) {
    clearTokens();
    return false;
  }
  setTokens(await r.json());
  return true;
}

async function request(path, { method = "GET", json, form, auth = true, blob = false, _retried = false } = {}) {
  const headers = {};
  if (auth && accessToken) headers.Authorization = `Bearer ${accessToken}`;
  let body;
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(json);
  } else if (form !== undefined) {
    body = form; // FormData — browser sets the multipart boundary
  }
  const r = await fetch(`${BASE}${path}`, { method, headers, body });
  if (r.status === 401 && auth && !_retried && (await refreshOnce())) {
    return request(path, { method, json, form, auth, blob, _retried: true });
  }
  if (!r.ok) {
    let detail;
    try {
      detail = (await r.json()).detail;
    } catch {
      detail = r.statusText;
    }
    throw new ApiError(r.status, detail);
  }
  if (r.status === 204) return null;
  if (blob) return r.blob();
  return r.json();
}

export const api = {
  get: (path) => request(path),
  getBlob: (path) => request(path, { blob: true }),
  post: (path, json) => request(path, { method: "POST", json }),
  put: (path, json) => request(path, { method: "PUT", json }),
  patch: (path, json) => request(path, { method: "PATCH", json }),
  postForm: (path, formData) => request(path, { method: "POST", form: formData }),
  public: {
    post: (path, json) => request(path, { method: "POST", json, auth: false }),
  },
};

/* FilePick keeps browser Files in memory keyed by their blob URL, so verbatim prototype
   components can pass {name, size, url} around while actions recover the raw File to upload. */
const FILE_REGISTRY = new Map();
export const registerFile = (url, raw) => FILE_REGISTRY.set(url, raw);
export const rawFromUrl = (url) => FILE_REGISTRY.get(url);

/* Short-lived download link for a stored file (Azure SAS or signed local URL). */
export async function openFileLink(fileId) {
  const { url } = await api.get(`/files/${fileId}/link`);
  const absolute = url.startsWith("http") ? url : `${BASE}${url}`;
  window.open(absolute, "_blank", "noopener");
}

export async function uploadFile(entity, entityId, rawFile) {
  const fd = new FormData();
  fd.append("entity", entity);
  fd.append("entity_id", entityId);
  fd.append("file", rawFile, rawFile.name);
  return api.postForm("/files", fd);
}
