import { host, PALETTE_AREA } from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

function verdict(run) {
  return run?.status || (run?.error ? 'ERROR' : run?.ok ? 'PASS' : 'FAIL')
}

function Card({ title, children }) {
  return jsxs('div', {
    className: 'rounded-md border border-(--ui-stroke-secondary) p-3',
    children: [
      jsx('div', { className: 'text-xs font-medium', children: title }),
      ...children
    ]
  })
}

function Run({ title, run }) {
  const scenario = run?.scenario
  return jsxs(Card, {
    title,
    children: [
      jsx('div', {
        className: 'mt-1 text-sm',
        children: `${verdict(run)} · ${run?.observed ?? '?'} observed / ${run?.expected ?? '?'} expected`
      }),
      (run?.error || run?.diagnosis)
        ? jsx('div', { className: 'mt-1 text-xs', children: run.error || run.diagnosis })
        : null,
      jsx('div', {
        className: 'mt-1 text-xs text-(--ui-text-tertiary)',
        children: run?.observations?.first_message
          ? `${run.observations.first_message} → ${run.observations.second_message ?? ''}`
          : Array.isArray(run?.records) ? `${run.records.length} persisted records` : 'No records measured'
      }),
      scenario
        ? jsx('div', {
            className: 'mt-1 text-xs text-(--ui-text-tertiary)',
            children: `scenario: ${scenario.valid ? 'valid' : 'invalid'} · upstream ${scenario.upstream_status ?? '?'} · committed ${scenario.committed_after_fault ?? '?'}`
          })
        : null
    ]
  })
}

function EvidencePane({ ctx }) {
  const [data, setData] = useState(null)
  const [cases, setCases] = useState([])
  const [selected, setSelected] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const refresh = () => Promise.all([ctx.rest('/overview'), ctx.rest('/contracts')])
    .then(([overview, list]) => {
      setData(overview)
      setCases(list.contracts || [])
      setSelected(current => current || ((list.contracts || []).find(item => !item.error && item.auto_start) || (list.contracts || []).find(item => !item.error))?.path || '')
      setError(null)
    }).catch(err => setError(String(err)))

  useEffect(() => {
    let alive = true
    Promise.all([ctx.rest('/overview'), ctx.rest('/contracts')])
      .then(([overview, list]) => {
        if (!alive) return
        setData(overview)
        setCases(list.contracts || [])
        const first = (list.contracts || []).find(item => !item.error && item.auto_start) || (list.contracts || []).find(item => !item.error)
        if (first) setSelected(first.path)
      })
      .catch((err) => { if (alive) setError(String(err)) })
    return () => { alive = false }
  }, [ctx])

  const startRun = async () => {
    if (!selected) return
    setBusy(true)
    setError(null)
    try {
      const result = await ctx.rest('/run', { method: 'POST', body: { contract_path: selected } })
      setData((current) => ({ ...(current || {}), latest_run: result }))
    } catch (err) {
      setError(String(err))
    } finally {
      setBusy(false)
    }
  }

  if (!data && !error) return jsx('div', { className: 'p-3 text-sm text-(--ui-text-tertiary)', children: 'Loading evidence…' })

  const selectedCase = cases.find(item => item.path === selected)
  const comparison = data?.comparison
  const latest = data?.latest_run
  const campaign = data?.campaign
  const items = []
  const caseOptions = cases.map((item) => jsx('option', {
    key: item.path,
    value: item.path,
    disabled: item.error ? true : undefined,
    children: item.error ? `${item.id} (invalid)` : `${item.id} · ${item.auto_start ? "starts app" : "setup required"}`
  }))
  if (!latest && !comparison && !campaign) {
    items.push(jsx('div', {
      key: 'empty',
      className: 'text-xs text-(--ui-text-tertiary)',
      children: 'No saved result yet. Select a case and run it.'
    }))
  }
  if (latest) {
    items.push(jsx(Run, {
      key: 'latest',
      title: latest.contract?.includes('kist') ? 'Last measured Kist run' : 'Last measured run',
      run: latest
    }))
    if (latest.control) {
      items.push(jsx(Run, { key: 'latest-control', title: 'Latest control', run: latest.control }))
    }
  }
  if (comparison) {
    items.push(jsx(Run, { key: 'buggy', title: 'Before repair', run: comparison.buggy }))
    items.push(jsx(Run, { key: 'fixed', title: 'After repair', run: comparison.fixed }))
  }
  if (campaign) {
    const counts = campaign.counts || {}
    items.push(jsxs(Card, {
      key: 'campaign',
      title: 'Capacity campaign',
      children: [
        jsx('div', {
          className: 'mt-1 text-sm',
          children: `${campaign.status ?? '?'} · ${counts.PASS ?? 0} pass / ${counts.FAIL ?? 0} fail / ${counts.INVALID ?? 0} invalid`
        }),
        jsx('div', {
          className: 'mt-1 text-xs text-(--ui-text-tertiary)',
          children: campaign.campaign ? `case set: ${campaign.campaign}` : 'recorded campaign result'
        })
      ]
    }))
  }

  return jsxs('div', {
    className: 'flex h-full flex-col gap-3 overflow-auto p-3 text-sm',
    children: [
      jsx('div', { className: 'font-medium', children: 'Test a saved journey' }),
      jsx('div', {
        className: 'text-xs text-(--ui-text-tertiary)',
        children: 'Run a case on a disposable local app and inspect what was saved.'
      }),
      jsxs('div', {
        className: 'flex items-center gap-2',
        children: [
          jsx('select', {
            'aria-label': 'Saved case',
            value: selected,
            onChange: (event) => setSelected(event.target.value),
            disabled: busy || caseOptions.length === 0,
            className: 'min-h-11 min-w-0 flex-1 rounded border border-(--ui-stroke-secondary) bg-transparent px-2 py-1 text-xs',
            children: caseOptions.length ? caseOptions : [jsx('option', { value: '', children: 'No cases available' })]
          }),
          jsx('button', {
            onClick: startRun,
            disabled: busy || !selected,
            className: 'min-h-11 rounded border border-(--ui-stroke-secondary) px-2 py-1 text-xs',
            children: busy ? 'Running…' : 'Run case'
          })
        ]
      }),
      selectedCase?.setup_required ? jsx('div', { role: 'status', children: selectedCase.setup_required }) : null,
      error ? jsx('div', { role: 'alert', className: 'text-sm text-(--ui-danger)', children: error }) : null,
      jsx('button', { onClick: refresh, disabled: busy, className: 'min-h-11 self-start rounded border px-3', children: 'Refresh cases and results' }),
      jsx('div', { 'aria-live': 'polite', className: 'flex flex-col gap-3', children: items }),
      jsx('div', {
        className: 'text-xs text-(--ui-text-tertiary)',
        children: 'Results cover configured assertions. HTTP observers are supplied by the app; SQLite observers read its database directly.'
      })
    ]
  })
}

export default {
  id: 'crashlab',
  name: 'CrashLab',
  defaultEnabled: false,
  register(ctx) {
    ctx.register({
      id: 'pane',
      area: 'panes',
      title: 'CrashLab',
      data: { placement: 'right', width: '380px' },
      render: () => jsx(EvidencePane, { ctx })
    })
    ctx.register({
      id: 'command',
      area: PALETTE_AREA,
      data: {
        id: 'crashlab.open',
        label: 'Open CrashLab evidence',
        keywords: ['crashlab', 'evidence', 'repair'],
        run: () => {
          if (typeof host.openWorkspace === 'function') {
            return host.openWorkspace('crashlab:evidence', { title: 'CrashLab', render: () => jsx(EvidencePane, { ctx }) })
          }
          host.notify({ kind: 'info', message: 'CrashLab evidence is in the right pane.' })
        }
      }
    })
  }
}
