import { host, PALETTE_AREA } from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

function verdict(value) {
  return value ? 'PASS' : 'FAIL'
}

function Run({ title, run }) {
  return jsxs('div', {
    className: 'rounded-md border border-(--ui-stroke-secondary) p-3',
    children: [
      jsx('div', { className: 'text-xs font-medium', children: title }),
      jsx('div', {
        className: 'mt-1 text-sm',
        children: `${verdict(run?.ok)} · ${run?.observed ?? '?'} observed / ${run?.expected ?? '?'} expected`
      }),
      jsx('div', {
        className: 'mt-1 text-xs text-(--ui-text-tertiary)',
        children: `${run?.observations?.first_message ?? ''} → ${run?.observations?.second_message ?? ''}`
      })
    ]
  })
}

function EvidencePane({ ctx }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let alive = true
    ctx.rest('/evidence')
      .then((value) => { if (alive) setData(value) })
      .catch((err) => { if (alive) setError(String(err)) })
    return () => { alive = false }
  }, [ctx])

  if (error) return jsx('div', { className: 'p-3 text-sm text-(--ui-danger)', children: error })
  if (!data) return jsx('div', { className: 'p-3 text-sm text-(--ui-text-tertiary)', children: 'Loading evidence…' })

  const repair = data.repair || {}
  return jsxs('div', {
    className: 'flex h-full flex-col gap-3 overflow-auto p-3 text-sm',
    children: [
      jsx('div', { className: 'font-medium', children: 'CrashLab evidence' }),
      jsx('div', {
        className: 'text-xs text-(--ui-text-tertiary)',
        children: `contract: ${repair.contract || 'none'} · overall: ${verdict(repair.ok)}`
      }),
      jsx(Run, { title: 'Buggy application', run: repair.buggy }),
      jsx(Run, { title: 'Repaired application', run: repair.fixed }),
      jsx(Run, { title: 'Control', run: repair.fixed?.control }),
      jsx('div', {
        className: 'text-xs text-(--ui-text-tertiary)',
        children: 'Records are read from /state. A pass requires observed = expected.'
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
        run: () => host.notify({ kind: 'info', message: 'CrashLab evidence is in the right pane.' })
      }
    })
  }
}
