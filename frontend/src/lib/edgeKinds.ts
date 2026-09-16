export const edgeKinds: Record<string, { label: string; color: string }> = {
  implementation: { label: '实现前置', color: '#69a28f' },
  migration: { label: '迁移前置', color: '#a58bc4' },
  verification: { label: '验证前置', color: '#78a8d2' },
};
export function edgeKind(kind?: string) {
  return edgeKinds[kind ?? 'implementation'] ?? edgeKinds.implementation!;
}
