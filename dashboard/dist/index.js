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

  const buttonStyle = { minHeight: 44, padding: '6px 10px', border: '1px solid var(--ui-stroke-secondary, #d4d4d8)', borderRadius: 4 }
  function RawDetails(label, value) {
    return h('details', {}, [
      h('summary', { key: 'summary', style: { minHeight: 44, paddingTop: 10, cursor: 'pointer' } }, label),
      h('pre', { key: 'value', style: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontSize: 12, maxHeight: 360, overflow: 'auto' }, tabIndex: 0 }, typeof value === 'string' ? value : JSON.stringify(value, null, 2))
    ])
  }
  function RecordedCase(item) {
    const result = item.result
    const events = result.events || []
    return h('details', { key: item.name, style: { border: '1px solid var(--ui-stroke-secondary, #d4d4d8)', borderRadius: 6, overflowWrap: 'anywhere' } }, [
      h('summary', { key: 'summary', style: { minHeight: 44, padding: 12, cursor: 'pointer', fontWeight: 600 } }, item.name.replaceAll('_', ' ') + ' · ' + verdict(result) + ' · ' + (result.observed ?? '?') + '/' + (result.expected ?? '?') + ' saved records' + (result.control ? ' · control ' + (result.control.ok ? 'PASS ' : 'FAIL ') + result.control.observed + '/' + result.control.expected : '')),
      h('div', { key: 'detail' }, Card('Measured evidence', [
      h('div', { key: 'outcome', style: { fontWeight: 600 } }, verdict(result) + ' · ' + (result.observed ?? '?') + '/' + (result.expected ?? '?') + ' saved records'),
      h('p', { key: 'diagnosis' }, result.diagnosis || result.error || 'No diagnosis was recorded.'),
      h('p', { key: 'recovery' }, result.recovery?.reason || 'No recovery evidence was recorded.'),
      h('div', { key: 'timeline' }, events.length ? [
        h('h4', { key: 'title', style: { fontWeight: 600, marginTop: 8 } }, 'Recorded execution'),
        h('ol', { key: 'events', style: { paddingLeft: 20, display: 'flex', flexDirection: 'column', gap: 8 } }, events.filter(event => event.phase === 'primary').map(event => {
          let line = event.kind.replaceAll('_', ' ')
          if (event.kind === 'write' || event.kind === 'reconciliation') line = event.method + ' ' + event.url + (event.status ? ' · HTTP ' + event.status : '')
          if (event.kind === 'interruption') line = (event.mode === 'prevent_before_delivery' ? 'Delivery prevented' : 'Acknowledgement suppressed') + (event.completed ? '' : ' · incomplete')
          if (event.kind === 'restart') line = event.completed ? 'App restarted · PID ' + event.lifecycle.old_pid + ' → ' + event.lifecycle.new_pid + ' · observed records retained' : 'Restart incomplete'
          if (event.state) line += ' · ' + event.state.observed + ' observed records'
          return h('li', { key: event.sequence }, [
            h('div', { key: 'line' }, event.sequence + '. ' + line),
            h('div', { key: 'raw' }, RawDetails('Event data · +' + event.elapsed_ms + ' ms', event))
          ])
        }))
      ] : h('p', {}, 'This historical run has no event timeline. Events are not reconstructed.')),
      h('div', { key: 'records' }, RawDetails('Saved records · IDs and fields', result.records || [])),
      h('p', { key: 'control' }, result.control ? 'Legitimate-use control: ' + (result.control.ok ? 'PASS' : 'FAIL') + ' · ' + result.control.observed + '/' + result.control.expected : 'Control was not completed.'),
      result.control ? h('div', { key: 'control-records' }, RawDetails('Control records and measured outcome', result.control)) : null,
      result.error ? h('p', { key: 'error', role: 'alert' }, result.error) : null
      ]))
    ])
  }
  function ExperimentReport({ data, currentStatus, onSelect, onExport, onClose, exporting, exportMessage }) {
    const measured = data.measurement
    const result = measured?.result
    return h('section', { 'aria-label': 'Experiment report', id: 'crashlab-experiment-report', tabIndex: -1, style: { display: 'flex', flexDirection: 'column', gap: 12, padding: 12, border: '1px solid var(--ui-stroke-secondary, #d4d4d8)', borderRadius: 6 } }, [
      h('h2', { key: 'title', style: { fontSize: 16, fontWeight: 600 } }, 'Recovery evidence'),
      h('p', { key: 'intent' }, data.intent),
      h('p', { key: 'current' }, 'Current source: ' + currentStatus),
      data.current_error ? h('p', { key: 'warning' }, data.current_error) : null,
      data.historical_only ? h('p', { key: 'historic' }, 'Historical evaluator: inspect these records, but prepare a new experiment before making a current verification claim.') : null,
      h('div', { key: 'buttons', style: { display: 'flex', flexWrap: 'wrap', gap: 8 } }, [
        h('button', { key: 'export', onClick: onExport, disabled: exporting || data.historical_only, style: buttonStyle }, exporting ? 'Exporting…' : 'Export regression'),
        h('button', { key: 'refresh-report', onClick: () => onSelect(data.run_index), style: buttonStyle }, 'Refresh report'),
        h('button', { key: 'close', onClick: onClose, style: buttonStyle }, 'Close report')
      ]),
      exportMessage ? h('p', { key: 'export-message', role: 'status' }, exportMessage) : null,
      data.runs.length ? h('label', { key: 'select-label', style: { display: 'flex', flexDirection: 'column', gap: 4 } }, [
        h('span', { key: 'label' }, 'Recorded run'),
        h('select', { key: 'select', value: data.run_index, onChange: event => onSelect(Number(event.target.value)), style: { minHeight: 44, padding: 8, minWidth: 0, maxWidth: '100%', background: 'transparent', border: '1px solid var(--ui-stroke-secondary, #d4d4d8)' } }, data.runs.map(run => h('option', { key: run.index, value: run.index }, (run.index + 1) + ' · ' + run.workflow_status + (run.recorded_at ? ' · ' + new Date(run.recorded_at).toLocaleString() : ''))))
      ]) : h('p', { key: 'empty' }, 'No measured run yet. Run the frozen case first.'),
      measured ? h('p', { key: 'measured' }, 'Recorded outcome: ' + measured.workflow_status + '. This is separate from current source freshness.') : null,
      ...(result ? (result.cases || [{ name: 'saved_operation', result }]).map(RecordedCase) : []),
      measured?.source_review ? h('div', { key: 'source' }, RawDetails('Recorded source change against Git HEAD', measured.source_review)) : null,
      h('div', { key: 'frozen' }, RawDetails('Unchanged expectations and case', data.frozen_case)),
      h('div', { key: 'identities' }, RawDetails('Case and evaluator identities', { contract_sha256: data.contract_sha256, evaluator_sha256: data.evaluator_sha256, project_sha256: measured?.project_sha256, replay: data.replay })),
      h('p', { key: 'scope', style: { color: 'var(--text-secondary, currentColor)', fontSize: 12 } }, data.scope)
    ])
  }

  function CrashLabPage() {
    const [overview, setOverview] = useState(null)
    const [cases, setCases] = useState([])
    const [experiments, setExperiments] = useState([])
    const [experimentError, setExperimentError] = useState(null)
    const [activeExperiment, setActiveExperiment] = useState(null)
    const loadSequence = useRef(0)
    const reportSequence = useRef(0)
    const reportLauncher = useRef(null)
    const statusCache = useRef({})
    const runningExperiment = useRef(null)
    const [reportData, setReportData] = useState(null)
    const [reportBusy, setReportBusy] = useState(false)
    const [reportError, setReportError] = useState('')
    const [exporting, setExporting] = useState(false)
    const [exportMessage, setExportMessage] = useState('')
    const [selected, setSelected] = useState('')
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState('')

    async function load() {
      const sequence = ++loadSequence.current
      try {
        const [state, list, repairs] = await Promise.all([api('/overview'), api('/contracts'), api('/experiments')])
        if (sequence !== loadSequence.current) return
        const freshRows = (repairs.experiments || []).map(row => row.experiment_id === runningExperiment.current ? { ...row, status: 'RUNNING' } : row)
        for (const row of freshRows) statusCache.current[row.experiment_id] = { sequence, status: row.status }
        setExperiments(freshRows)
        setReportData(current => {
          if (!current) return current
          const row = freshRows.find(item => item.experiment_id === current.experiment_id)
          return { ...current, current_status: row?.status || 'UNAVAILABLE', current_error: row?.error || (row ? null : 'Use Refresh report to check this experiment outside the recent list.') }
        })
        setOverview(state)
        const rows = list.contracts || []
        setCases(rows)
        setSelected(current => current || ((rows.find(row => !row.error && row.auto_start) || rows.find(row => !row.error) || {}).path || ''))
        setError('')
      } catch (err) {
        if (sequence !== loadSequence.current) return
        setError(String(err.message || err))
        setExperiments(rows => rows.map(row => ({ ...row, status: 'UNAVAILABLE' })))
        setReportData(current => current ? { ...current, current_status: 'UNAVAILABLE' } : current)
      }
    }

    useEffect(() => {
      const refresh = () => {
        if (document.visibilityState === 'hidden') return
        // A return event must supersede even an unfinished pre-edit request.
        // loadSequence prevents that older response from replacing current proof.
        load()
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

    useEffect(() => {
      if (!reportData) return
      const section = document.getElementById('crashlab-experiment-report')
      section?.focus({ preventScroll: true })
      section?.scrollIntoView({ behavior: 'auto', block: 'start' })
    }, [reportData?.experiment_id, reportData?.run_index, reportData?.measurement])

    async function experimentAction(row, action) {
      setBusy(true)
      setExperimentError(null)
      setActiveExperiment({ id: row.experiment_id, action })
      if (action === 'run') {
        runningExperiment.current = row.experiment_id
        setExperiments(rows => rows.map(item => item.experiment_id === row.experiment_id ? { ...item, status: 'RUNNING' } : item))
        setReportData(current => current?.experiment_id === row.experiment_id ? { ...current, current_status: 'RUNNING' } : current)
      }
      try {
        const result = await api('/experiments/' + row.experiment_id + '/' + action, {
          method: 'POST', body: JSON.stringify(action === 'watch' ? { enabled: !row.watching } : {})
        })
        runningExperiment.current = null
        if (action === 'watch') setExperiments(rows => rows.map(item => item.experiment_id === row.experiment_id ? { ...item, watching: !!result.watching } : item))
        await load()
        if (action === 'run' && reportData?.experiment_id === row.experiment_id) await showReport(row.experiment_id)
      } catch (err) {
        runningExperiment.current = null
        setExperiments(rows => rows.map(item => item.experiment_id === row.experiment_id ? { ...item, status: 'UNAVAILABLE' } : item))
        await load()
        setExperimentError({ id: row.experiment_id, message: String(err.message || err) })
      } finally { runningExperiment.current = null; setBusy(false); setActiveExperiment(null) }
    }

    async function showReport(id, index) {
      const sequence = ++reportSequence.current
      const freshnessSequence = ++loadSequence.current
      setReportData(null); setReportBusy(true); setReportError(''); setExportMessage('')
      try {
        const report = await api('/experiments/' + id + '/report' + (index === undefined || index === null ? '' : '?run_index=' + index))
        if (sequence === reportSequence.current) {
          const cached = statusCache.current[id]
          if (!cached || cached.sequence <= freshnessSequence) statusCache.current[id] = { sequence: freshnessSequence, status: runningExperiment.current === id ? 'RUNNING' : report.current_status }
          const currentStatus = statusCache.current[id].status
          setExperiments(rows => rows.map(row => row.experiment_id === id ? { ...row, status: currentStatus } : row))
          setReportData({ ...report, current_status: currentStatus })
        }
      } catch (err) { if (sequence === reportSequence.current) setReportError(String(err.message || err)) }
      finally { if (sequence === reportSequence.current) setReportBusy(false) }
    }
    async function exportReport() {
      const sequence = reportSequence.current
      setExporting(true); setExportMessage('')
      try {
        const exported = await api('/experiments/' + reportData.experiment_id + '/export', { method: 'POST', body: '{}' })
        const url = URL.createObjectURL(new Blob([JSON.stringify(exported.bundle, null, 2)], { type: 'application/json' }))
        const link = document.createElement('a'); link.href = url; link.download = 'crashlab-regression-' + reportData.experiment_id + '.json'; link.click()
        setTimeout(() => URL.revokeObjectURL(url), 1000)
        if (sequence === reportSequence.current) setExportMessage('Frozen regression exported. Import with Hermes using the same evaluator and your local project/interpreter paths. Prior VERIFIED status is not transferred.')
      } catch (err) { if (sequence === reportSequence.current) setExportMessage('Export failed: ' + String(err.message || err)) }
      finally { setExporting(false) }
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
        h('p', { key: 'help', style: { color: 'var(--text-secondary, currentColor)' } }, 'Ask Hermes to check your local app and describe the behavior you expect. Your five most recent experiments and all watched experiments appear here.'),
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
            h('button', { key: 'report', onClick: event => { reportLauncher.current = event.currentTarget; showReport(row.experiment_id) }, 'aria-controls': 'crashlab-experiment-report', 'aria-expanded': reportData?.experiment_id === row.experiment_id, disabled: busy, style: buttonStyle }, 'Inspect evidence'),
            h('button', { key: 'watch', disabled: busy, onClick: () => experimentAction(row, 'watch'), style: { minHeight: 44, padding: '6px 10px', border: '1px solid var(--ui-stroke-secondary, #d4d4d8)', borderRadius: 4 } }, activeExperiment?.id === row.experiment_id && activeExperiment.action === 'watch' ? 'Updating reminders…' : row.watching ? 'Stop completion reminders' : 'Watch edits')
          ]),
          h('div', { key: 'id', style: { overflowWrap: 'anywhere', fontSize: 12, color: 'var(--text-secondary, currentColor)' } }, 'Experiment ' + row.experiment_id)
        ]))),
        !experiments.length ? h('p', { key: 'empty' }, 'No repair experiment yet. Ask Hermes to prepare one for your local app.') : null
      ]),
      reportBusy ? h('p', { key: 'report-loading', role: 'status' }, 'Loading recorded evidence…') : null,
      reportError ? h('p', { key: 'report-error', role: 'alert' }, reportError) : null,
      reportData ? h(ExperimentReport, { key: 'report', data: reportData, currentStatus: reportData.current_status, onSelect: index => showReport(reportData.experiment_id, index), onExport: exportReport, onClose: () => { reportSequence.current++; setReportData(null); requestAnimationFrame(() => reportLauncher.current?.focus()) }, exporting, exportMessage }) : null,
      h('h2', { key: 'examples', style: { fontSize: 16, fontWeight: 600 } }, 'Run a saved case'),
      h('p', { key: 'example-note', style: { color: 'var(--text-secondary, currentColor)' } }, 'Bundled checkout and booking cases are deliberately buggy/fixed pairs. A failure on a buggy example is expected.'),
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
