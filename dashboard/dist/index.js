/* CrashLab dashboard tab. Plain IIFE so the dashboard needs no build step. */
(function () {
  'use strict'
  const SDK = window.__HERMES_PLUGIN_SDK__
  if (!SDK || !window.__HERMES_PLUGINS__) return
  const React = SDK.React
  const h = React.createElement
  const { useEffect, useState } = React

  async function api(path, options) {
    const response = await SDK.authedFetch('/api/plugins/crashlab' + path, {
      headers: { 'Content-Type': 'application/json' },
      ...options
    })
    const body = await response.json().catch(() => ({ ok: false, error: 'invalid response' }))
    if (!response.ok) throw new Error(body.detail || body.error || 'request failed')
    return body
  }

  function verdict(run) {
    return run.status || (run.error ? 'ERROR' : run.ok ? 'PASS' : 'FAIL')
  }

  function Card(title, children) {
    return h('div', {
      style: {
        border: '1px solid var(--ui-stroke-secondary, #d4d4d8)',
        borderRadius: 6,
        padding: 12,
        display: 'flex',
        flexDirection: 'column',
        gap: 4
      }
    }, [
      h('div', { key: 'title', style: { fontSize: 14, fontWeight: 600 } }, title),
      ...children
    ])
  }

  function EvidenceCard(title, run) {
    if (!run) return null
    return Card(title, [
      h('div', { key: 'verdict', style: { fontSize: 14 } },
        verdict(run) + ' · ' + (run.observed ?? '?') + ' observed / ' + (run.expected ?? '?') + ' expected'),
      run.diagnosis ? h('div', { key: 'diagnosis', style: { fontSize: 14, color: 'var(--ui-text-secondary, #52525b)' } }, run.diagnosis) : null,
      run.error ? h('div', { key: 'error', role: 'alert' }, run.error) : null,
      h('div', { key: 'control', style: { fontSize: 13 } }, run.control ? 'Legitimate-use control: ' + verdict(run.control) + ' · ' + run.control.observed + '/' + run.control.expected : run.control === null && !run.error ? 'No legitimate-use control configured' : 'Control not completed / result unavailable'),
      run.contract ? h('div', { key: 'contract', style: { fontSize: 13, color: 'var(--ui-text-tertiary, #71717a)' } }, 'case: ' + run.contract) : null
    ])
  }

  function CrashLabPage() {
    const [overview, setOverview] = useState(null)
    const [cases, setCases] = useState([])
    const [selected, setSelected] = useState('')
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState('')

    async function load() {
      try {
        const [state, list] = await Promise.all([api('/overview'), api('/contracts')])
        setOverview(state)
        const rows = list.contracts || []
        setCases(rows)
        setSelected(current => current || ((rows.find(row => !row.error && row.auto_start) || rows.find(row => !row.error) || {}).path || ''))
        setError('')
      } catch (err) {
        setError(String(err.message || err))
      }
    }

    useEffect(() => { load() }, [])

    async function startRun() {
      if (!selected) return
      setBusy(true)
      setError('')
      try {
        await api('/run', { method: 'POST', body: JSON.stringify({ contract_path: selected }) })
        await load()
      } catch (err) {
        setError(String(err.message || err))
      } finally {
        setBusy(false)
      }
    }

    const latest = overview && overview.latest_run
    const comparison = overview && overview.comparison
    const campaign = overview && overview.campaign
    const items = []
    if (latest) items.push(h('div', { key: 'latest' }, EvidenceCard('Latest run', latest)))
    if (comparison) {
      items.push(h('div', { key: 'before' }, EvidenceCard('Before repair', comparison.buggy)))
      items.push(h('div', { key: 'after' }, EvidenceCard('After repair', comparison.fixed)))
    }
    if (campaign) {
      const counts = campaign.counts || {}
      items.push(Card('Capacity campaign', [
        h('div', { key: 'line', style: { fontSize: 14 } },
          (campaign.status ?? '?') + ' · ' + (counts.PASS ?? 0) + ' pass / ' + (counts.FAIL ?? 0) + ' fail / ' + (counts.INVALID ?? 0) + ' invalid'),
        h('div', { key: 'case', style: { fontSize: 13, color: 'var(--ui-text-tertiary, #71717a)' } },
          campaign.campaign ? 'case set: ' + campaign.campaign : 'recorded campaign result')
      ]))
    }
    if (!items.length) {
      items.push(h('div', { key: 'empty', style: { fontSize: 13, color: 'var(--ui-text-secondary, #52525b)' } }, overview ? 'No saved result yet. Select a case and run it.' : 'Loading cases…'))
    }

    return h('div', { style: { display: 'flex', height: '100%', minHeight: 0, flexDirection: 'column', gap: 12, overflow: 'auto', padding: 16, fontSize: 14 } }, [
      h('div', { key: 'heading', style: { fontSize: 18, fontWeight: 600 } }, 'Test a saved journey'),
      h('div', { key: 'sub', style: { fontSize: 14, color: 'var(--ui-text-tertiary, #71717a)' } },
        'Run a case on a disposable local app. Check what was saved after an interrupted action.'),
      h('div', { key: 'controls', style: { display: 'flex', gap: 8, alignItems: 'center' } }, [
        h('select', {
          key: 'select',
          'aria-label': 'Saved case',
          value: selected,
          disabled: busy || !cases.length,
          onChange: event => setSelected(event.target.value),
          style: { flex: 1, minWidth: 0, minHeight: 44, padding: '6px 8px', borderRadius: 4, border: '1px solid var(--ui-stroke-secondary, #d4d4d8)', background: 'transparent' }
        }, cases.length ? cases.map(row => h('option', { key: row.path, value: row.path, disabled: !!row.error }, row.id + (row.auto_start ? ' · starts app' : ' · setup required'))) : [h('option', { key: 'none', value: '' }, 'No cases available')]),
        h('button', {
          key: 'run',
          onClick: startRun,
          disabled: busy || !selected,
          style: { minHeight: 44, padding: '6px 10px', borderRadius: 4, border: '1px solid var(--ui-stroke-secondary, #d4d4d8)', cursor: busy ? 'wait' : 'pointer' }
        }, busy ? 'Running…' : 'Run case')
      ]),
      error ? h('div', { key: 'error', role: 'alert', style: { fontSize: 14, color: '#b91c1c' } }, error) : null,
      h('button', { key: 'refresh', onClick: load, disabled: busy, style: { alignSelf: 'flex-start', minHeight: 44, padding: '6px 10px' } }, 'Refresh cases and results'),
      h('div', { key: 'results', 'aria-live': 'polite' }, items),
      h('div', { key: 'foot', style: { fontSize: 13, color: 'var(--ui-text-tertiary, #71717a)' } },
        'Results cover the configured assertions. HTTP observers are supplied by the app; SQLite observers read its database directly.')
    ])
  }

  window.__HERMES_PLUGINS__.register('crashlab', CrashLabPage)
})()
