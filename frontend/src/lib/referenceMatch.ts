export function fuzzyMatch(value: string, query: string) {
  if (!query) return true;
  const candidate = value.toLocaleLowerCase();
  const needle = query.toLocaleLowerCase();
  let cursor = 0;
  for (const character of needle) {
    cursor = candidate.indexOf(character, cursor);
    if (cursor < 0) return false;
    cursor += 1;
  }
  return true;
}
