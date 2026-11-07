/* CrashLab dashboard tab. Plain IIFE so the dashboard needs no build step. */
(function () {
  'use strict'
  const SDK = window.__HERMES_PLUGIN_SDK__
  if (!SDK || !window.__HERMES_PLUGINS__) return
  const React = SDK.React
  const h = React.createElement
  const { useEffect, useState, useRef } = React

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
        overflowWrap: 'anywhere',
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
      run.diagnosis ? h('div', { key: 'diagnosis', style: { fontSize: 14, color: 'var(--text-secondary, currentColor)' } }, run.diagnosis) : null,
      run.error ? h('div', { key: 'error', role: 'alert' }, run.error) : null,
      h('div', { key: 'control', style: { fontSize: 13 } }, run.control ? 'Legitimate-use control: ' + verdict(run.control) + ' · ' + run.control.observed + '/' + run.control.expected : run.control === null && !run.error ? 'No legitimate-use control configured' : 'Control not completed / result unavailable'),
      run.contract ? h('div', { key: 'contract', style: { fontSize: 13, color: 'var(--text-secondary, currentColor)' } }, 'case: ' + run.contract) : null
    ])
  }

  function CrashLabPage() {
    const [overview, setOverview] = useState(null)
    const [cases, setCases] = useState([])
    const [experiments, setExperiments] = useState([])
    const [experimentError, setExperimentError] = useState(null)
    const [activeExperiment, setActiveExperiment] = useState(null)
    const loadSequence = useRef(0)
    const [selected, setSelected] = useState('')
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState('')

    async function load() {
      const sequence = ++loadSequence.current
      try {
        const [state, list, repairs] = await Promise.all([api('/overview'), api('/contracts'), api('/experiments')])
        if (sequence !== loadSequence.current) return
        setExperiments(repairs.experiments || [])
        setOverview(state)
        const rows = list.contracts || []
        setCases(rows)
        setSelected(current => current || ((rows.find(row => !row.error && row.auto_start) || rows.find(row => !row.error) || {}).path || ''))
        setError('')
      } catch (err) {
        if (sequence !== loadSequence.current) return
        setError(String(err.message || err))
        setExperiments(rows => rows.map(row => ({ ...row, status: 'UNAVAILABLE' })))
      }
    }

    useEffect(() => {
      let refreshing = false
      const refresh = async () => {
        if (document.visibilityState === 'hidden' || refreshing) return
        refreshing = true
        try { await load() } finally { refreshing = false }
      }
      refresh()
      window.addEventListener('focus', refresh)
      document.addEventListener('visibilitychange', refresh)
      return () => {
        window.removeEventListener('focus', refresh)
        document.removeEventListener('visibilitychange', refresh)
        loadSequence.current++
      }
    }, [])

    async function experimentAction(row, action) {
      setBusy(true)
      setExperimentError(null)
      setActiveExperiment({ id: row.experiment_id, action })
      try {
        const result = await api('/experiments/' + row.experiment_id + '/' + action, {
          method: 'POST', body: JSON.stringify(action === 'watch' ? { enabled: !row.watching } : {})
        })
        if (action === 'watch') setExperiments(rows => rows.map(item => item.experiment_id === row.experiment_id ? { ...item, watching: !!result.watching } : item))
        await load()
      } catch (err) {
        setExperiments(rows => rows.map(item => item.experiment_id === row.experiment_id ? { ...item, status: 'UNAVAILABLE' } : item))
        await load()
        setExperimentError({ id: row.experiment_id, message: String(err.message || err) })
      } finally { setBusy(false); setActiveExperiment(null) }
    }

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

    const selectedCase = cases.find(row => row.path === selected)
    const latest = overview && overview.latest_run
    const comparison = overview && overview.comparison
    const campaign = overview && overview.campaign
    const items = []
    if (latest) items.push(h('div', { key: 'latest' }, EvidenceCard('Last measured run', latest)))
    if (comparison) {
      items.push(h('div', { key: 'before' }, EvidenceCard('Before repair', comparison.buggy)))
      items.push(h('div', { key: 'after' }, EvidenceCard('After repair', comparison.fixed)))
    }
    if (campaign) {
      const counts = campaign.counts || {}
      items.push(Card('Capacity campaign', [
        h('div', { key: 'line', style: { fontSize: 14 } },
          (campaign.status ?? '?') + ' · ' + (counts.PASS ?? 0) + ' pass / ' + (counts.FAIL ?? 0) + ' fail / ' + (counts.INVALID ?? 0) + ' invalid'),
        h('div', { key: 'case', style: { fontSize: 13, color: 'var(--text-secondary, currentColor)' } },
          campaign.campaign ? 'case set: ' + campaign.campaign : 'recorded campaign result')
      ]))
    }
    if (!items.length) {
      items.push(h('div', { key: 'empty', style: { fontSize: 13, color: 'var(--text-secondary, currentColor)' } }, overview ? 'No saved result yet. Select a case and run it.' : 'Loading cases…'))
    }

    return h('div', { style: { display: 'flex', height: '100%', minHeight: 0, flexDirection: 'column', gap: 12, overflow: 'auto', padding: 16, fontSize: 14 } }, [
      h('div', { key: 'heading', style: { fontSize: 18, fontWeight: 600 } }, 'Verify an app repair'),
      h('div', { key: 'sub', style: { fontSize: 14, color: 'var(--text-secondary, currentColor)' } },
        'Repair your local app, then rerun the same frozen case to check what it saved.'),
      h('section', { key: 'experiments', 'aria-label': 'Repair experiments', style: { display: 'flex', flexDirection: 'column', gap: 12 } }, [
        h('h2', { key: 'title', style: { fontSize: 16, fontWeight: 600 } }, 'Your repair experiments'),
        h('p', { key: 'help', style: { color: 'var(--text-secondary, currentColor)' } }, 'Ask Hermes to check your local app and describe the behavior you expect. Your five most recent experiments appear here.'),
        ...experiments.map(row => h('article', { key: row.experiment_id, 'aria-label': 'Experiment ' + row.experiment_id }, Card(row.intent || 'Repair experiment', [
          h('div', { key: 'status', role: 'status', style: { fontWeight: 600 } }, row.status),
          row.project_path ? h('div', { key: 'project', style: { overflowWrap: 'anywhere', color: 'var(--text-secondary, currentColor)' } }, row.project_path) : null,
          row.status === 'STALE' ? h('p', { key: 'stale' }, 'The app changed after the last test. Rerun this frozen case before relying on its result.') : null,
          row.runs?.length ? h('div', { key: 'last-result' }, [
            h('div', { key: 'counts' }, 'Last measured: ' + row.runs.at(-1).result.status + ' · ' + (row.runs.at(-1).result.observed ?? '?') + '/' + (row.runs.at(-1).result.expected ?? '?')),
            h('div', { key: 'diagnosis' }, row.runs.at(-1).result.diagnosis),
            row.runs.at(-1).result.control ? h('div', { key: 'control' }, 'Control: ' + (row.runs.at(-1).result.control.ok ? 'PASS' : 'FAIL') + ' · ' + row.runs.at(-1).result.control.observed + '/' + row.runs.at(-1).result.control.expected) : null
          ]) : null,
          row.error ? h('div', { key: 'error', role: 'alert' }, row.error) : null,
          row.error_detail ? h('details', { key: 'detail' }, [h('summary', { key: 'summary', style: { minHeight: 44, paddingTop: 10 } }, 'Technical details'), h('div', { key: 'body' }, row.error_detail)]) : null,
          experimentError?.id === row.experiment_id ? h('div', { key: 'action-error', role: 'alert' }, experimentError.message) : null,
          h('div', { key: 'buttons', style: { display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 } }, [
            h('button', { key: 'run', disabled: busy, onClick: () => experimentAction(row, 'run'), style: { minHeight: 44, padding: '6px 10px', border: '1px solid var(--ui-stroke-secondary, #d4d4d8)', borderRadius: 4 } }, activeExperiment?.id === row.experiment_id && activeExperiment.action === 'run' ? 'Running experiment…' : 'Rerun frozen case'),
            h('button', { key: 'watch', disabled: busy, onClick: () => experimentAction(row, 'watch'), style: { minHeight: 44, padding: '6px 10px', border: '1px solid var(--ui-stroke-secondary, #d4d4d8)', borderRadius: 4 } }, activeExperiment?.id === row.experiment_id && activeExperiment.action === 'watch' ? 'Updating reminders…' : row.watching ? 'Stop completion reminders' : 'Watch edits')
          ]),
          h('div', { key: 'id', style: { overflowWrap: 'anywhere', fontSize: 12, color: 'var(--text-secondary, currentColor)' } }, 'Experiment ' + row.experiment_id)
        ]))),
        !experiments.length ? h('p', { key: 'empty' }, 'No repair experiment yet. Ask Hermes to prepare one for your local app.') : null
      ]),
      h('h2', { key: 'examples', style: { fontSize: 16, fontWeight: 600 } }, 'Run a saved example'),
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
        }, busy && !activeExperiment ? 'Running…' : 'Run case')
      ]),
      selectedCase?.setup_required ? h('div', { key: 'setup', role: 'status' }, selectedCase.setup_required) : null,
      error ? h('div', { key: 'error', role: 'alert', style: { fontSize: 14, color: 'var(--text-primary, currentColor)' } }, error) : null,
      h('button', { key: 'refresh', onClick: load, disabled: busy, style: { alignSelf: 'flex-start', minHeight: 44, padding: '6px 10px' } }, 'Refresh cases and results'),
      h('div', { key: 'results', 'aria-live': 'polite' }, items),
      h('div', { key: 'foot', style: { fontSize: 13, color: 'var(--text-secondary, currentColor)' } },
        'Results cover the configured assertions. HTTP observers are supplied by the app; SQLite observers read its database directly.')
    ])
  }

  window.__HERMES_PLUGINS__.register('crashlab', CrashLabPage)
})()
