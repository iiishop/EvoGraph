import { createHash } from 'node:crypto';
import { lstatSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { dirname, join, relative, resolve, sep } from 'node:path';

export const manifestName = '.evograph-build.json';
// Keep in step with frontend_build.py; the cross-language test checks this contract.
const sourcePaths = [
  'frontend',
  'package.json',
  'package-lock.json',
  'tsconfig.json',
  'vite.config.ts',
  'tools/frontend_provenance.mjs',
  'tools/frontend_provenance.d.mts',
];

function exists(path) {
  try {
    return lstatSync(path);
  } catch (error) {
    if (error.code === 'ENOENT') return null;
    throw error;
  }
}

function verifyDependencies(root) {
  const read = (path) => JSON.parse(readFileSync(join(root, path), 'utf8'));
  const manifest = read('package.json');
  const hasLock = exists(join(root, 'package-lock.json'));
  const locked = hasLock ? read('package-lock.json').packages || {} : {};
  const installed = exists(join(root, 'node_modules/.package-lock.json'))
    ? read('node_modules/.package-lock.json').packages || {}
    : {};
  const groups = ['dependencies', 'devDependencies'];
  const required = new Set(
    groups.flatMap((group) =>
      Object.keys(manifest[group] || {}).map((name) => `node_modules/${name}`),
    ),
  );
  const issues = [];
  if (
    locked[''] &&
    groups.some((group) => {
      const wanted = manifest[group] || {};
      const recorded = locked[''][group] || {};
      return (
        Object.keys(wanted).length !== Object.keys(recorded).length ||
        Object.keys(wanted).some((name) => wanted[name] !== recorded[name])
      );
    })
  )
    issues.push('package.json differs from package-lock.json');
  for (const name of new Set([...required, ...Object.keys(locked)])) {
    if (!name.startsWith('node_modules/') || name.split('/').includes('..')) continue;
    const expected = locked[name] || {};
    if (expected.link) continue;
    if (!exists(join(root, name, 'package.json'))) {
      if (required.has(name) || !expected.optional) issues.push(`${name} is missing`);
      continue;
    }
    const actual = read(`${name}/package.json`);
    const recorded = installed[name] || {};
    if (
      (expected.version && actual.version !== expected.version) ||
      ['version', 'integrity'].some(
        (field) => expected[field] && recorded[field] && expected[field] !== recorded[field],
      )
    ) {
      issues.push(`${name} differs from package-lock.json`);
    }
  }
  if (issues.length) {
    throw new Error(
      `Frontend dependencies are missing or out of date:\n${issues.join('\n')}\n` +
        `Run ${hasLock ? 'npm ci' : 'npm install'} in ${root}, then npm run build.`,
    );
  }
}

export function fileHashes(root, paths, exclude = '') {
  const files = {};
  function visit(path) {
    const stat = exists(path);
    if (!stat) return;
    const name = relative(root, path).split(sep).join('/');
    if (name === exclude) return;
    if (stat.isSymbolicLink()) throw new Error(`Cannot verify symbolic link: ${path}`);
    if (stat.isDirectory()) {
      for (const entry of readdirSync(path)) visit(join(path, entry));
    } else if (stat.isFile()) {
      files[name] = createHash('sha256').update(readFileSync(path)).digest('hex');
    }
  }
  for (const path of paths) visit(join(root, path));
  return files;
}

export function sourceHashes(root) {
  return fileHashes(root, sourcePaths);
}

export function sourceHash(files) {
  const hash = createHash('sha256');
  const names = Object.keys(files).sort((a, b) => Buffer.compare(Buffer.from(a), Buffer.from(b)));
  for (const name of names) hash.update(`${name}\0${files[name]}\n`);
  return hash.digest('hex');
}

export function frontendProvenance() {
  let projectRoot;
  let outputRoot;
  let sources;
  return {
    name: 'evograph-frontend-provenance',
    apply: 'build',
    enforce: 'post',
    configResolved(config) {
      projectRoot = dirname(config.configFile);
      outputRoot = resolve(config.root, config.build.outDir);
    },
    buildStart() {
      verifyDependencies(projectRoot);
      sources = sourceHashes(projectRoot);
      const expected = process.env.EVOGRAPH_EXPECTED_FRONTEND_SOURCE_HASH;
      if (expected && sourceHash(sources) !== expected) {
        this.error('Frontend inputs changed before Vite started. Run the build again.');
      }
    },
    writeBundle() {
      if (sourceHash(sources) !== sourceHash(sourceHashes(projectRoot))) {
        this.error('Frontend inputs changed during the build. Run npm run build again.');
      }
      verifyDependencies(projectRoot);
      const outputs = fileHashes(outputRoot, ['.'], manifestName);
      if (!outputs['index.html']) this.error('Frontend build did not produce index.html.');
      const manifest = {
        schema: 1,
        version: JSON.parse(readFileSync(join(projectRoot, 'package.json'), 'utf8')).version,
        builtAt: new Date().toISOString(),
        sourceHash: sourceHash(sources),
        sources,
        outputs,
      };
      writeFileSync(join(outputRoot, manifestName), `${JSON.stringify(manifest, null, 2)}\n`);
    },
  };
}
