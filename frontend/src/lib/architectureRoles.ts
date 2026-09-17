export const architectureRoles = {
  frontend: { label: '前端', color: '#087e91', tint: '#e7f6f9' },
  backend: { label: '服务', color: '#18765c', tint: '#eaf7f1' },
  database: { label: '数据', color: '#7751c4', tint: '#f1ecfb' },
  security: { label: '安全', color: '#be4564', tint: '#fcecf1' },
  cloud: { label: '基础设施', color: '#966315', tint: '#fff6df' },
  message: { label: '消息', color: '#b35627', tint: '#fff0e5' },
  external: { label: '外部系统', color: '#596980', tint: '#edf1f7' },
} as const;
export function architectureRole(role?: string) {
  return architectureRoles[role as keyof typeof architectureRoles] ?? architectureRoles.backend;
}
