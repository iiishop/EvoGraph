import type { SourceClassModel, SourceLocation, SourceRelation } from '../types';

export const sourceRelationLabels: Record<SourceRelation['kind'], string> = {
  extends: '继承声明',
  implements: '实现声明',
  import: '包级导入',
  architecture: '架构关系',
};
export function sourceLocationLabel(location: SourceLocation) {
  return `${location.path}:${location.line}${location.end_line > location.line ? `–${location.end_line}` : ''}`;
}

const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);
const text = (value: unknown, max = 1024, empty = false): value is string =>
  typeof value === 'string' && (empty || value.length > 0) && value.length <= max;
const strings = (value: unknown, max: number, min = 0): value is string[] =>
  Array.isArray(value) &&
  value.length >= min &&
  value.length <= max &&
  value.every((item) => text(item));
const list = (value: unknown, max: number, min = 0): value is unknown[] =>
  Array.isArray(value) && value.length >= min && value.length <= max;
const oneOf = (value: unknown, values: readonly string[]) =>
  typeof value === 'string' && values.includes(value);

/** Total validation of an additive JSON payload before replacing the standard diagram. */
export function validSourceClassModel(model: unknown): model is SourceClassModel {
  if (
    !record(model) ||
    model.schema_version !== 1 ||
    model.origin !== 'source' ||
    !text(model.project_id) ||
    !text(model.baseline_id) ||
    !Number.isInteger(model.architecture_revision) ||
    Number(model.architecture_revision) < 0 ||
    !strings(model.component_ids, 3, 1) ||
    new Set(model.component_ids).size !== model.component_ids.length ||
    !strings(model.files, 12, 1) ||
    new Set(model.files).size !== model.files.length ||
    !strings(model.limitations, 100) ||
    !record(model.source_fingerprints) ||
    Object.keys(model.source_fingerprints).length !== model.files.length ||
    !list(model.classes, 24, 1) ||
    !list(model.packages, 3, 1) ||
    !list(model.boundaries, 24) ||
    !list(model.relations, 1024)
  )
    return false;
  const files = new Set(model.files);
  const fingerprints = model.source_fingerprints;
  if (
    !model.files.every((file) => Object.hasOwn(fingerprints, file) && text(fingerprints[file], 128))
  )
    return false;
  const locationValid = (value: unknown) =>
    record(value) &&
    text(value.path) &&
    files.has(value.path) &&
    Number.isSafeInteger(value.line) &&
    Number(value.line) > 0 &&
    Number.isSafeInteger(value.end_line) &&
    Number(value.end_line) >= Number(value.line);
  const ids = new Set<string>();
  for (const node of [...model.classes, ...model.packages, ...model.boundaries]) {
    if (!record(node) || !text(node.id) || ids.has(node.id)) return false;
    ids.add(node.id);
  }
  const packages = new Set<string>();
  for (const item of model.packages) {
    if (
      !record(item) ||
      !text(item.id) ||
      !text(item.component_id) ||
      !model.component_ids.includes(item.component_id) ||
      !text(item.label)
    )
      return false;
    packages.add(item.id);
  }
  const members = new Set<string>();
  for (const item of model.classes) {
    if (
      !record(item) ||
      !text(item.name) ||
      (item.declaration !== undefined && !text(item.declaration, 400)) ||
      !text(item.kind, 40) ||
      !oneOf(item.language, ['python', 'cpp', 'typescript', 'javascript']) ||
      !locationValid(item.location) ||
      !list(item.members, 32) ||
      !strings(item.package_ids, 3, 1) ||
      item.package_ids.some((id) => !packages.has(id))
    )
      return false;
    for (const member of item.members) {
      if (
        !record(member) ||
        !text(member.id) ||
        members.has(member.id) ||
        !text(member.name) ||
        !locationValid(member.location) ||
        !oneOf(member.kind, ['field', 'method']) ||
        !text(member.text, 400) ||
        !oneOf(member.visibility, ['public', 'protected', 'private', 'unspecified']) ||
        !strings(member.qualifiers, 32)
      )
        return false;
      members.add(member.id);
    }
  }
  if (members.size > 160) return false;
  for (const item of model.boundaries) {
    if (
      !record(item) ||
      !text(item.label) ||
      !oneOf(item.kind, ['import', 'base', 'architecture']) ||
      !oneOf(item.origin, ['source', 'design']) ||
      (item.reason !== undefined && !text(item.reason))
    )
      return false;
  }
  const relations = new Set<string>();
  for (const item of model.relations) {
    if (
      !record(item) ||
      !text(item.id) ||
      relations.has(item.id) ||
      !text(item.source) ||
      !ids.has(item.source) ||
      !text(item.target) ||
      !ids.has(item.target) ||
      !oneOf(item.kind, ['extends', 'implements', 'import', 'architecture']) ||
      !text(item.label, 2000, true) ||
      !oneOf(item.origin, ['source', 'design']) ||
      (item.resolution !== undefined &&
        !oneOf(item.resolution, ['selected', 'boundary', 'architecture'])) ||
      (item.location !== undefined && !locationValid(item.location))
    )
      return false;
    relations.add(item.id);
  }
  return true;
}
