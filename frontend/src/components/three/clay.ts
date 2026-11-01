// Общее для 3D-сцен в стиле «глина» (диорама на входе и момент решения): свет запечён в текстуру в Blender,
// поэтому в браузере нужны только «плоские» материалы без освещения, а камера — изометрическая ортографическая.
import {
  Box3, Color, Matrix4, Mesh, MeshBasicMaterial, Object3D, Quaternion, Vector3,
  type Material, type MeshStandardMaterial, type OrthographicCamera,
} from 'three'

// Ракурс из Blender (build_diorama.py): азимут 30°, возвышение 33°. В three ось Y смотрит вверх, а Blender-у «вглубь» соответствует -Z.
const AZ = (30 * Math.PI) / 180
const EL = (33 * Math.PI) / 180
export const ISO_DIR = new Vector3(-Math.sin(AZ) * Math.cos(EL), Math.sin(EL), Math.cos(AZ) * Math.cos(EL)) // от цели к камере
const quat = new Quaternion().setFromRotationMatrix(new Matrix4().lookAt(ISO_DIR, new Vector3(), new Vector3(0, 1, 0)))
const RIGHT = new Vector3(1, 0, 0).applyQuaternion(quat)
const UP = new Vector3(0, 1, 0).applyQuaternion(quat)

export type Baked = { mat: MeshBasicMaterial; mesh: Mesh }

/** Заменяет материалы модели на MeshBasicMaterial: запечённые — с текстурой-атласом, Fx_* — плоским цветом (иногда полупрозрачным). */
export function toBasic(root: Object3D): Baked[] {
  const baked: Baked[] = []
  const cache = new Map<Material, MeshBasicMaterial>()
  root.traverse((o) => {
    const mesh = o as Mesh
    if (!mesh.isMesh) return
    const src = mesh.material as MeshStandardMaterial
    let m = cache.get(src)
    // материал на мешах общий, поэтому для подсветки станций делаем копию на каждый меш
    if (!m || src.map) {
      m = src.map
        ? new MeshBasicMaterial({ map: src.map, toneMapped: false })
        : new MeshBasicMaterial({ color: src.color, toneMapped: false, transparent: src.opacity < 1, opacity: src.opacity, depthWrite: src.opacity >= 1 })
      cache.set(src, m)
    }
    mesh.material = m
    if (src.map) baked.push({ mat: m, mesh })
  })
  return baked
}

export type Fit = { ppu: number; target: Vector3 }

/** Масштаб (пикселей на единицу) и точка, на которую смотрит камера, чтобы коробка box поместилась в долю fitW×fitH холста
 * и её центр оказался в точке (cx, cy) холста (-1…1, y вверх). */
export function fitIso(box: Box3, width: number, height: number, fitW: number, fitH: number, cx: number, cy: number): Fit {
  const c = box.getCenter(new Vector3())
  let rMin = Infinity, rMax = -Infinity, uMin = Infinity, uMax = -Infinity
  for (let i = 0; i < 8; i++) {
    const p = new Vector3(i & 1 ? box.max.x : box.min.x, i & 2 ? box.max.y : box.min.y, i & 4 ? box.max.z : box.min.z).sub(c)
    const r = p.dot(RIGHT), u = p.dot(UP)
    rMin = Math.min(rMin, r); rMax = Math.max(rMax, r); uMin = Math.min(uMin, u); uMax = Math.max(uMax, u)
  }
  const ppu = Math.max(1, Math.min((fitW * width) / (rMax - rMin), (fitH * height) / (uMax - uMin)))
  // центр проекции коробки (не центр объёма) — в нужную точку экрана
  const pc = RIGHT.clone().multiplyScalar((rMax + rMin) / 2).addScaledVector(UP, (uMax + uMin) / 2).add(c)
  const target = pc.sub(RIGHT.clone().multiplyScalar(((cx * width) / 2) / ppu)).sub(UP.clone().multiplyScalar(((cy * height) / 2) / ppu))
  return { ppu, target }
}

export function applyIsoCamera(cam: OrthographicCamera, width: number, height: number, fit: Fit) {
  cam.left = -width / 2; cam.right = width / 2; cam.top = height / 2; cam.bottom = -height / 2
  cam.near = 0.1; cam.far = 400
  cam.zoom = fit.ppu
  cam.position.copy(fit.target).addScaledVector(ISO_DIR, 100)
  cam.quaternion.copy(quat)
  cam.updateProjectionMatrix()
  cam.updateMatrixWorld()
}

export const lightTint = new Color('#ffffff')
export const darkTint = new Color('#8f95bd') // тёмная тема: приглушённая «бумага» с холодным оттенком, чтобы остров не слепил
