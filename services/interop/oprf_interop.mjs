// Проверка совместимости: браузерная библиотека @cloudflare/voprf-ts ↔ Go-токенизатор (circl/oprf).
//
// Повторяет шаги раздела 5 контракта, как это делает браузер клиента:
//   паспорт → ослепление → запрос в токенизатор (с HMAC-подписью, как backend) → проверка доказательства DLEQ →
//   снятие ослепления → passport_hash = hex(SHA-256("sad-passport-v1" ‖ выход OPRF))[:16].
// Делает это дважды и убеждается, что хэш одинаковый, а blinded разные (сервер не может связать две попытки).
//
// Запуск (токенизатор должен работать: bin/tokenizer или bash scripts/run.sh):
//   node services/interop/oprf_interop.mjs
// Переменные: TOKENIZER_URL (http://127.0.0.1:8103), TOKENIZER_HMAC_SECRET (демо-значение),
//             FRONTEND_DIR (папка с node_modules/@cloudflare/voprf-ts, по умолчанию ./frontend).
import { createHash, createHmac } from 'node:crypto'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const frontendDir = process.env.FRONTEND_DIR ?? path.join(here, '..', '..', 'frontend')
const require = createRequire(path.join(frontendDir, 'package.json'))
const { Oprf, VOPRFClient, Evaluation, DLEQProof } = require('@cloudflare/voprf-ts')

const URL_BASE = process.env.TOKENIZER_URL ?? 'http://127.0.0.1:8103'
const SECRET = process.env.TOKENIZER_HMAC_SECRET ?? 'dev-tokenizer-secret'
const PASSPORT = '4510123456' // нормализованный номер: 10 цифр
const SUITE = Oprf.Suite.P256_SHA256

const b64 = (bytes) => Buffer.from(bytes).toString('base64')
const unb64 = (text) => new Uint8Array(Buffer.from(text, 'base64'))

// та же подпись, что в common/signing.py: hex HMAC-SHA256(секрет, "<ts>.<тело>")
function signedHeaders(body) {
  const ts = Math.floor(Date.now() / 1000)
  const sig = createHmac('sha256', SECRET).update(`${ts}.`).update(body).digest('hex')
  return { 'X-Signature': sig, 'X-Timestamp': String(ts) }
}

function checkResponseSignature(res, body) {
  const ts = res.headers.get('x-timestamp')
  const expected = createHmac('sha256', SECRET).update(`${ts}.`).update(body).digest('hex')
  if (res.headers.get('x-signature') !== expected) throw new Error('подпись ответа токенизатора неверна')
}

function fail(message) {
  console.error('ОШИБКА:', message)
  process.exit(1)
}

async function call(method, route, payload) {
  const body = payload ? JSON.stringify(payload) : ''
  const res = await fetch(URL_BASE + route, {
    method,
    headers: { 'Content-Type': 'application/json', ...(payload ? signedHeaders(body) : {}) },
    body: payload ? body : undefined,
  })
  const text = await res.text()
  if (!res.ok) fail(`${method} ${route}: ${res.status} ${text}`)
  checkResponseSignature(res, text)
  return JSON.parse(text)
}

// Один проход «браузера»: возвращает {passportHash, blinded}
async function tokenize(publicKey) {
  const client = new VOPRFClient(SUITE, publicKey)
  const [finData, evalReq] = await client.blind([new TextEncoder().encode(PASSPORT)])
  const blinded = evalReq.blinded[0].serialize(true) // сжатая точка, 33 байта

  const reply = await call('POST', '/oprf/v1/evaluate', { blinded: b64(blinded) })

  const group = Oprf.getGroup(SUITE)
  const evaluated = group.eltDes.deserialize(unb64(reply.evaluated))
  const proof = DLEQProof.deserialize(group.id, unb64(reply.proof))
  // finalize сам проверяет доказательство DLEQ открытым ключом и бросает ошибку, если оно неверное
  const [output] = await client.finalize(finData, new Evaluation(Oprf.Mode.VOPRF, [evaluated], proof))
  if (output.length !== 32) fail(`выход OPRF ${output.length} байт, ждали 32`)

  const passportHash = createHash('sha256')
    .update(Buffer.concat([Buffer.from('sad-passport-v1'), Buffer.from(output)]))
    .digest('hex')
    .slice(0, 16)
  return { passportHash, blinded: b64(blinded) }
}

const pk = await call('GET', '/oprf/v1/public-key')
if (pk.suite !== 'P256-SHA256') fail(`набор ${pk.suite}, ждали P256-SHA256`)
const publicKey = unb64(pk.public_key)
console.log(`открытый ключ: ${pk.suite}, ${publicKey.length} байт`)

const first = await tokenize(publicKey)
const second = await tokenize(publicKey)
console.log('попытка 1: passport_hash =', first.passportHash, ' blinded =', first.blinded.slice(0, 20) + '…')
console.log('попытка 2: passport_hash =', second.passportHash, ' blinded =', second.blinded.slice(0, 20) + '…')

if (!/^[a-f0-9]{16}$/.test(first.passportHash)) fail('passport_hash не в формате ^[a-f0-9]{16}$')
if (first.passportHash !== second.passportHash) fail('хэш одного паспорта отличается между попытками')
if (first.blinded === second.blinded) fail('blinded одинаковые — ослепление не случайно')
console.log('OK: доказательство проверено, хэш стабилен, blinded различаются')
