import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import {
  fileHashes,
  frontendProvenance,
  manifestName,
  sourceHash,
  sourceHashes,
} from '../../tools/frontend_provenance.mjs';

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'evograph-build-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  mkdirSync(join(root, 'frontend', 'public'), { recursive: true });
  mkdirSync(join(root, 'dist', 'assets'), { recursive: true });
  writeFileSync(join(root, 'frontend', 'index.html'), '<main>source</main>');
  writeFileSync(join(root, 'frontend', 'public', 'logo.svg'), '<svg/>');
  writeFileSync(join(root, 'package.json'), JSON.stringify({ version: '0.2.0' }));
  writeFileSync(join(root, 'dist', 'index.html'), '<main>built</main>');
  writeFileSync(join(root, 'dist', 'assets', 'main.js'), 'console.log("built")');
  const plugin = frontendProvenance();
  plugin.configResolved({
    configFile: join(root, 'vite.config.ts'),
    root: join(root, 'frontend'),
    build: { outDir: '../dist' },
  });
  plugin.error = (message) => {
    throw new Error(message);
  };
  return { root, plugin };
}

test('Vite records source, lock, public and output identities after a successful write', (t) => {
  const { root, plugin } = fixture(t);
  writeFileSync(join(root, 'package-lock.json'), '{}');
  plugin.buildStart();
  plugin.writeBundle();
  const manifest = JSON.parse(readFileSync(join(root, 'dist', manifestName), 'utf8'));
  assert.equal(manifest.schema, 1);
  assert.equal(manifest.version, '0.2.0');
  assert.deepEqual(manifest.sources, sourceHashes(root));
  assert.equal(manifest.sourceHash, sourceHash(manifest.sources));
  assert.ok(manifest.sources['frontend/public/logo.svg']);
  assert.ok(manifest.sources['package-lock.json']);
  assert.deepEqual(manifest.outputs, fileHashes(join(root, 'dist'), ['.'], manifestName));
  assert.equal(manifest.outputs[manifestName], undefined);
});

test('a source change during the Vite build fails instead of recording false provenance', (t) => {
  const { root, plugin } = fixture(t);
  plugin.buildStart();
  writeFileSync(join(root, 'frontend', 'index.html'), '<main>edited mid-build</main>');
  assert.throws(() => plugin.writeBundle(), /inputs changed/);
  assert.throws(() => readFileSync(join(root, 'dist', manifestName)), /ENOENT/);
});

test('incomplete output cannot be stamped as a successful build', (t) => {
  const { root, plugin } = fixture(t);
  plugin.buildStart();
  rmSync(join(root, 'dist', 'index.html'));
  assert.throws(() => plugin.writeBundle(), /did not produce index.html/);
});

test('source identity is independent of object insertion order', () => {
  assert.equal(sourceHash({ a: '1', z: '2' }), sourceHash({ z: '2', a: '1' }));
});

function dependencies(root) {
  const manifest = { version: '0.2.0', dependencies: { direct: '1.0.0' } };
  const packages = {
    '': manifest,
    'node_modules/direct': { version: '1.0.0', integrity: 'direct-current' },
    'node_modules/transitive': { version: '2.0.0', integrity: 'transitive-current' },
    'node_modules/optional-win32': { version: '1.0.0', optional: true, os: ['win32'] },
  };
  writeFileSync(join(root, 'package.json'), JSON.stringify(manifest));
  writeFileSync(join(root, 'package-lock.json'), JSON.stringify({ packages }));
  for (const name of ['direct', 'transitive']) {
    mkdirSync(join(root, 'node_modules', name), { recursive: true });
    writeFileSync(
      join(root, 'node_modules', name, 'package.json'),
      JSON.stringify({ version: packages[`node_modules/${name}`].version }),
    );
  }
  writeFileSync(join(root, 'node_modules/.package-lock.json'), JSON.stringify({ packages }));
}

test('manual npm build validates installed direct and transitive dependency versions', (t) => {
  const { root, plugin } = fixture(t);
  dependencies(root);
  for (const name of ['direct', 'transitive']) {
    const file = join(root, 'node_modules', name, 'package.json');
    const original = readFileSync(file);
    writeFileSync(file, '{"version":"0.1.0"}');
    assert.throws(() => plugin.buildStart(), /differs from package-lock.json[\s\S]*npm ci/);
    writeFileSync(file, original);
  }
  plugin.buildStart();
  plugin.writeBundle(); // Missing optional packages for other OSes remain normal.
});

test('manual build checks missing packages and lock/install identity without installing', (t) => {
  const { root, plugin } = fixture(t);
  dependencies(root);
  const hidden = join(root, 'node_modules/.package-lock.json');
  const installed = JSON.parse(readFileSync(hidden));
  installed.packages['node_modules/transitive'].integrity = 'old-content-same-version';
  writeFileSync(hidden, JSON.stringify(installed));
  assert.throws(() => plugin.buildStart(), /transitive.*differs/);
  rmSync(hidden); // Older npm installations need not have hidden lock metadata.
  plugin.buildStart();
  rmSync(join(root, 'node_modules/direct/package.json'));
  assert.throws(() => plugin.writeBundle(), /direct.*missing/);
});

test('manual build refuses dependency manifest/lock disagreement', (t) => {
  const { root, plugin } = fixture(t);
  dependencies(root);
  writeFileSync(
    join(root, 'package.json'),
    JSON.stringify({ version: '0.2.0', dependencies: { direct: '2.0.0' } }),
  );
  assert.throws(() => plugin.buildStart(), /package.json differs/);
});

test('launcher fingerprint rejects edits during type-checking or Vite config loading', (t) => {
  const { root, plugin } = fixture(t);
  const key = 'EVOGRAPH_EXPECTED_FRONTEND_SOURCE_HASH';
  const previous = process.env[key];
  t.after(() => {
    if (previous === undefined) delete process.env[key];
    else process.env[key] = previous;
  });
  process.env[key] = sourceHash(sourceHashes(root));
  plugin.buildStart();
  writeFileSync(join(root, 'vite.config.ts'), 'changed after npm began, before Vite started');
  assert.throws(() => plugin.buildStart(), /changed before Vite started/);
  assert.throws(() => readFileSync(join(root, 'dist', manifestName)), /ENOENT/);
});
