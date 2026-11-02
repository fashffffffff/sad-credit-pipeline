// Какие экраны бэк-офиса видит каждая роль — по матрице RBAC (раздел 6.1). Порядок = порядок в меню.
import type { StaffRole } from './types'

export type StaffPage = 'queue' | 'apps' | 'batch' | 'audit' | 'external' | 'monitor' | 'lab'
export const STAFF_PAGES: Record<StaffRole, StaffPage[]> = {
  underwriter: ['queue', 'apps', 'batch', 'external', 'monitor'],
  auditor: ['audit', 'external', 'apps', 'monitor'],
  admin: ['monitor', 'lab', 'batch', 'apps'],
}
export const STAFF_HOME: Record<StaffRole, StaffPage> = { underwriter: 'queue', auditor: 'audit', admin: 'monitor' }
