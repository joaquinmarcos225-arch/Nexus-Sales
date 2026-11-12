import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildFirmographyRows,
  normalizeDataOrigin,
} from './prospectFirmography.js'

test('buildFirmographyRows omits empty fields', () => {
  const rows = buildFirmographyRows({
    name: 'Ana',
    industry: 'SaaS',
    industry_origin: 'prospeo',
  })
  assert.equal(rows.length, 1)
  assert.equal(rows[0].label, 'Industria')
  assert.equal(rows[0].provenanceLabel, 'Observado (Prospeo)')
})

test('missing firmography does not produce placeholder rows', () => {
  assert.deepEqual(buildFirmographyRows({ name: 'Ana' }), [])
})

test('unknown origin is labeled no confirmado', () => {
  const o = normalizeDataOrigin('uncertain')
  assert.equal(o.kind, 'unknown')
  assert.equal(o.label, 'No confirmado')
})
