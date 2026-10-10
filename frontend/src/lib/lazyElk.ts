import type { ELK } from 'elkjs/lib/elk.bundled.js';

/** Keep each consumer's engine, but load ELK only when it has a graph to lay out. */
export function createLazyElk(): Pick<ELK, 'layout'> {
  let engine: Promise<ELK> | undefined;
  return {
    async layout(graph, options) {
      engine ??= import('elkjs/lib/elk.bundled.js')
        .then(({ default: ELK }) => new ELK())
        .catch((error) => {
          // Let a later request retry initialization without hiding this failure.
          engine = undefined;
          throw error;
        });
      return (await engine).layout(graph, options);
    },
  };
}
