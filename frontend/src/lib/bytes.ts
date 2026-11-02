// Мелкие помощники для байтов: hex и base64 (в том числе base64url без «=»).
export const toHex = (b: Uint8Array) => Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('')
export const toB64 = (b: Uint8Array) => btoa(String.fromCharCode(...b))
export const fromB64 = (s: string) => Uint8Array.from(atob(s.replace(/-/g, '+').replace(/_/g, '/')), (c) => c.charCodeAt(0))
