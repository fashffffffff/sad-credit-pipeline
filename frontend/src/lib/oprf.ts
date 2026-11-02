// OPRF-маскировка паспорта (раздел 5 контракта, RFC 9497, режим VOPRF, набор P256-SHA256).
// Браузер ослепляет номер случайным числом, сервер подписывает «слепое» значение своим ключом и не видит номер,
// браузер проверяет доказательство, снимает ослепление и получает 32 байта. Из них делается passport_hash.
import { Evaluation, EvaluationRequest, Oprf, VOPRFClient } from '@cloudflare/voprf-ts'
import { fromB64, toB64, toHex } from './bytes'
import type { OprfResponse, PassportPublicKey } from './types'

const SUITE = Oprf.Suite.P256_SHA256
const PREFIX = 'sad-passport-v1'


/** Что видит каждая сторона на каждом шаге — этим кормится анимация «что видит сервер». */
export interface OprfSteps {
  /** 10 цифр: остаются только на устройстве */
  digits: string
  /** то, что ушло на сервер (hex первых байт и полное значение) */
  blindedHex: string
  /** то, что вернул сервер */
  evaluatedHex: string
  proofHex: string
  /** выход OPRF после снятия ослепления: на устройстве */
  outputHex: string
  /** итог: 16 hex-символов, единственное, что попадёт в заявку */
  passportHash: string
}

export interface OprfServer {
  publicKey(): Promise<PassportPublicKey>
  evaluate(blinded: string): Promise<OprfResponse>
}

export type OprfStage = 'blind' | 'server' | 'verify' | 'unblind' | 'hash'

/** Сборка ответа сервера в формат voprf-ts: [число точек (u16)] [точка] [режим] [доказательство c‖s]. */
function evaluationFromWire(evaluated: Uint8Array, proof: Uint8Array): Evaluation {
  const bytes = new Uint8Array(2 + evaluated.length + 1 + proof.length)
  bytes.set([0, 1], 0)
  bytes.set(evaluated, 2)
  bytes[2 + evaluated.length] = Oprf.Mode.VOPRF
  bytes.set(proof, 3 + evaluated.length)
  return Evaluation.deserialize(SUITE, bytes)
}

async function sha256(data: Uint8Array): Promise<Uint8Array> {
  return new Uint8Array(await crypto.subtle.digest('SHA-256', data as BufferSource))
}

/** Весь путь от номера до passport_hash. onStage вызывается перед каждым шагом (для анимации). */
export async function tokenizePassport(digits: string, server: OprfServer, onStage?: (s: OprfStage, partial: Partial<OprfSteps>) => void | Promise<void>): Promise<OprfSteps> {
  if (!/^\d{10}$/.test(digits)) throw new Error('Паспорт должен состоять из 10 цифр')
  if (!crypto?.subtle) throw new Error('Для защиты паспорта нужно безопасное соединение (https). Откройте приложение по https-адресу.')
  const steps: Partial<OprfSteps> = { digits }

  const pub = await server.publicKey()
  const client = new VOPRFClient(SUITE, fromB64(pub.public_key))

  // 1. ослепляем: blinded = H(паспорт)^r, r — случайное число, которое знает только браузер
  await onStage?.('blind', steps)
  const [finData, req] = await client.blind([new TextEncoder().encode(digits)])
  const blinded = req.blinded[0].serialize()
  steps.blindedHex = toHex(blinded)

  // 2. сервер умножает на секретный ключ (он видит только случайную точку)
  await onStage?.('server', steps)
  const resp = await server.evaluate(toB64(blinded))
  const evaluated = fromB64(resp.evaluated)
  const proof = fromB64(resp.proof)
  steps.evaluatedHex = toHex(evaluated)
  steps.proofHex = toHex(proof)

  // 3. проверяем доказательство (DLEQ): сервер использовал именно свой опубликованный ключ
  await onStage?.('verify', steps)
  // 4. снимаем ослепление (finalize проверяет доказательство и бросает ошибку, если оно неверно)
  await onStage?.('unblind', steps)
  let output: Uint8Array
  try {
    ;[output] = await client.finalize(finData, evaluationFromWire(evaluated, proof))
  } catch {
    throw new Error('Не удалось проверить ответ сервера (доказательство не сошлось). Попробуйте ещё раз.')
  }
  steps.outputHex = toHex(output)

  // 5. passport_hash = hex(SHA-256("sad-passport-v1" ‖ выход))[:16]
  await onStage?.('hash', steps)
  const prefix = new TextEncoder().encode(PREFIX)
  const joined = new Uint8Array(prefix.length + output.length)
  joined.set(prefix, 0)
  joined.set(output, prefix.length)
  steps.passportHash = toHex(await sha256(joined)).slice(0, 16)
  return steps as OprfSteps
}

// ---- «сервер» для мок-режима: настоящий VOPRF с ключом, созданным в браузере (хранится в localStorage, чтобы хэши не менялись) ----
import { VOPRFServer, generatePublicKey, randomPrivateKey } from '@cloudflare/voprf-ts'

const MOCK_KEY = 'sad-mock-oprf-key'
let mockServer: Promise<{ server: VOPRFServer; pub: Uint8Array }> | null = null
function getMockServer() {
  mockServer ??= (async () => {
    let sk: Uint8Array
    const saved = localStorage.getItem(MOCK_KEY)
    if (saved) sk = fromB64(saved)
    else { sk = await randomPrivateKey(SUITE); localStorage.setItem(MOCK_KEY, toB64(sk)) }
    return { server: new VOPRFServer(SUITE, sk), pub: generatePublicKey(SUITE, sk) }
  })()
  return mockServer
}
export async function mockOprfPublicKey(): Promise<PassportPublicKey> {
  return { suite: 'P256-SHA256', public_key: toB64((await getMockServer()).pub) }
}
export async function mockOprfEvaluate(blinded: string): Promise<OprfResponse> {
  const { server } = await getMockServer()
  const point = fromB64(blinded)
  const bytes = new Uint8Array(2 + point.length)
  bytes.set([0, 1], 0)
  bytes.set(point, 2)
  const ev = await server.blindEvaluate(EvaluationRequest.deserialize(SUITE, bytes))
  return { evaluated: toB64(ev.evaluated[0].serialize()), proof: toB64(ev.proof!.serialize()) }
}
/** passport_hash для известного номера без протокола: нужен только моку, чтобы узнавать демо-паспорта. */
export async function mockHashFor(digits: string): Promise<string> {
  const { server } = await getMockServer()
  const out = await server.evaluate(new TextEncoder().encode(digits))
  const prefix = new TextEncoder().encode(PREFIX)
  const joined = new Uint8Array(prefix.length + out.length)
  joined.set(prefix, 0); joined.set(out, prefix.length)
  return toHex(await sha256(joined)).slice(0, 16)
}
