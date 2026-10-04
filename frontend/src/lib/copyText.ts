/** Synchronous copy has a definite result even in embedded WebViews whose
 * permission-based Clipboard API can leave a promise pending indefinitely.
 * It never reads the clipboard or asks the host to change its permissions.
 */
export function copyText(text: string): boolean {
  const previous = document.activeElement;
  const field = document.createElement('textarea');
  field.value = text;
  field.readOnly = true;
  field.setAttribute('aria-hidden', 'true');
  Object.assign(field.style, { position: 'fixed', left: '-10000px', top: '0', opacity: '0' });
  document.body.append(field);
  try {
    field.focus({ preventScroll: true });
    field.select();
    return document.execCommand('copy');
  } catch {
    return false;
  } finally {
    field.remove();
    if (previous instanceof HTMLElement && previous.isConnected)
      previous.focus({ preventScroll: true });
  }
}
