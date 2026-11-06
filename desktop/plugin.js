import { host, PALETTE_AREA } from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

function verdict(value) {
  return value ? 'PASS' : 'FAIL'
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
        children: `${verdict(run?.ok)} · ${run?.observed ?? '?'} observed / ${run?.expected ?? '?'} expected`
      }),
      jsx('div', {
        className: 'mt-1 text-xs text-(--ui-text-tertiary)',
        children: run?.observations?.first_message
          ? `${run.observations.first_message} → ${run.observations.second_message ?? ''}`
          : `${run?.records?.length ?? 0} persisted records`
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
  const [error, setError] = useState(null)

  useEffect(() => {
    let alive = true
    ctx.rest('/overview')
      .then((value) => { if (alive) setData(value) })
      .catch((err) => { if (alive) setError(String(err)) })
    return () => { alive = false }
  }, [ctx])

  if (error) return jsx('div', { className: 'p-3 text-sm text-(--ui-danger)', children: error })
  if (!data) return jsx('div', { className: 'p-3 text-sm text-(--ui-text-tertiary)', children: 'Loading evidence…' })

  const comparison = data.comparison
  const latest = data.latest_run
  const campaign = data.campaign
  if (!comparison && !latest && !campaign) {
    return jsx('div', {
      className: 'p-3 text-sm text-(--ui-text-tertiary)',
      children: 'No saved result yet. Run crashlab_run or crashlab_compare first.'
    })
  }

  const items = []
  if (latest) {
    items.push(jsx(Run, {
      key: 'latest',
      title: latest.contract?.includes('kist') ? 'External Kist run' : 'Latest run',
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
      jsx('div', { className: 'font-medium', children: 'CrashLab evidence' }),
      jsx('div', {
        className: 'text-xs text-(--ui-text-tertiary)',
        children: 'A pass requires a valid scenario, observed = expected, and a passing control.'
      }),
      ...items,
      jsx('div', {
        className: 'text-xs text-(--ui-text-tertiary)',
        children: 'Records are read through the configured observer; the app cannot write the verdict.'
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
