import { useState } from 'react'
import UpgradePrompt from '../UpgradePrompt/UpgradePrompt'
import { FREE_TIER } from '../../config'

const CATEGORY_META = {
  icebreakers:     { label: 'Icebreakers',    icon: '🌊', var: '--accent-icebreakers' },
  truth:           { label: 'Truth',          icon: '🔍', var: '--accent-truth' },
  dares:           { label: 'Dares',          icon: '🔥', var: '--accent-dares' },
  compliments:     { label: 'Compliments',    icon: '💛', var: '--accent-compliments' },
  dirty:           { label: 'Dirty',          icon: '💋', var: '--accent-dirty' },
  deep:            { label: 'Deep',           icon: '🌌', var: '--accent-deep' },
  party:           { label: 'Party',          icon: '🎉', var: '--accent-party' },
  debate:          { label: 'Debate',         icon: '⚡', var: '--accent-truth' },
  freshers:        { label: 'Freshers',       icon: '🎒', var: '--accent-icebreakers' },
  hot_takes:       { label: 'Hot Takes',      icon: '🌶️', var: '--accent-dares' },
  would_you_rather:{ label: 'Would U Rather', icon: '🤔', var: '--accent-deep' },
}

export default function CardCategories({ packs, onSelect, onMixStart, onBack, unlockedPacks, devMode, mode }) {
  const [upgradeReason, setUpgradeReason] = useState(null)
  const [mixActive, setMixActive] = useState(false)
  const [selectedDecks, setSelectedDecks] = useState(new Set())

  const handleMixToggle = () => {
    if (FREE_TIER && !devMode) {
      setUpgradeReason('mix_decks')
      return
    }
    setMixActive(m => !m)
    setSelectedDecks(new Set())
  }

  const toggleDeck = (pack) => {
    const locked = FREE_TIER && !devMode && unlockedPacks !== null && !unlockedPacks.has(pack.id)
    if (locked) {
      setUpgradeReason('locked_deck')
      return
    }
    setSelectedDecks(s => {
      const next = new Set(s)
      if (next.has(pack.id)) next.delete(pack.id)
      else next.add(pack.id)
      return next
    })
  }

  const selectedPacks = packs.filter(p => selectedDecks.has(p.id))
  const totalMixCards = selectedPacks.reduce((sum, p) => sum + (p.cards?.length ?? 0), 0)

  return (
    <div style={{
      minHeight: '100dvh',
      background: 'var(--bg-floor)',
      padding: 'var(--safe-margin)',
      paddingTop: 'calc(env(safe-area-inset-top, 0px) + 20px)',
      paddingBottom: 'calc(var(--thumb-zone) + 80px)',
    }}>
      <button
        onClick={onBack}
        style={{
          background: 'none',
          border: 'none',
          color: 'var(--on-surface-dim)',
          fontFamily: 'var(--font-mono)',
          fontSize: '14px',
          cursor: 'pointer',
          padding: '8px 0',
          marginBottom: '12px',
          minHeight: '56px',
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
        }}
      >
        ← Back
      </button>

      {/* Heading row with mix toggle */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
        <h1 style={{
          fontFamily: 'var(--font-headline)',
          fontWeight: 900,
          fontSize: 'clamp(24px, 7vw, 32px)',
          color: 'var(--on-surface)',
          margin: 0,
        }}>
          {mixActive ? 'Mix Decks' : 'Choose a deck'}
        </h1>
        <button
          onClick={handleMixToggle}
          style={{
            background: mixActive ? 'var(--primary-glow)' : 'var(--glass-bg)',
            backdropFilter: 'blur(8px)',
            WebkitBackdropFilter: 'blur(8px)',
            border: `1px solid ${mixActive ? 'var(--primary-glow)' : 'rgba(255,255,255,0.12)'}`,
            borderRadius: '100px',
            padding: '8px 14px',
            fontFamily: 'var(--font-mono)',
            fontSize: '12px',
            fontWeight: 700,
            color: mixActive ? '#ffffff' : 'var(--on-surface-dim)',
            cursor: 'pointer',
            whiteSpace: 'nowrap',
            minHeight: '36px',
          }}
          onTouchStart={e => e.currentTarget.style.opacity = '0.8'}
          onTouchEnd={e => e.currentTarget.style.opacity = '1'}
        >
          {mixActive ? 'Done' : 'Mix'}
        </button>
      </div>

      <p style={{
        fontFamily: 'var(--font-body)',
        color: 'var(--on-surface-dim)',
        fontSize: '14px',
        margin: '0 0 24px',
      }}>
        {mixActive ? 'Select 2+ decks to combine.' : 'Pick a category to play.'}
      </p>

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(2, 1fr)',
        gap: '12px',
      }}>
        {packs.map(pack => {
          const meta = CATEGORY_META[pack.id] ?? { label: pack.name, icon: pack.icon ?? '🃏' }
          const locked = FREE_TIER && !devMode && unlockedPacks !== null && !unlockedPacks.has(pack.id)
          const isSelected = mixActive && selectedDecks.has(pack.id)
          const accent = locked
            ? 'rgba(255,255,255,0.2)'
            : (meta.var ? `var(${meta.var})` : (pack.accent ?? 'var(--primary)'))
          // In mix mode, selected cards use the purple/primary theme regardless of category
          const cardBorder = mixActive && isSelected
            ? 'var(--primary)'
            : (locked ? 'rgba(255,255,255,0.2)' : accent)

          return (
            <button
              key={pack.id}
              onClick={() => mixActive ? toggleDeck(pack) : (locked ? setUpgradeReason('locked_deck') : onSelect(pack))}
              style={{
                background: mixActive && isSelected ? 'color-mix(in srgb, var(--primary) 10%, var(--glass-bg))' : 'var(--glass-bg)',
                backdropFilter: 'blur(var(--glass-blur))',
                WebkitBackdropFilter: 'blur(var(--glass-blur))',
                border: `1.5px solid ${cardBorder}`,
                borderRadius: '16px',
                padding: '20px 16px',
                cursor: 'pointer',
                textAlign: 'left',
                minHeight: '120px',
                display: 'flex',
                flexDirection: 'column',
                gap: '8px',
                boxShadow: locked && !isSelected ? 'none' : (mixActive && isSelected ? '0 0 20px color-mix(in srgb, var(--primary) 40%, transparent)' : `0 0 16px ${accent}33`),
                transition: 'box-shadow 0.2s, transform 0.15s',
                opacity: locked ? 0.45 : 1,
                position: 'relative',
              }}
              onTouchStart={e => e.currentTarget.style.transform = 'scale(0.97)'}
              onTouchEnd={e => e.currentTarget.style.transform = 'scale(1)'}
            >
              {mixActive ? (
                <span style={{
                  position: 'absolute',
                  top: '10px',
                  right: '12px',
                  width: '18px',
                  height: '18px',
                  borderRadius: '50%',
                  border: `2px solid ${isSelected ? '#ffffff' : 'rgba(255,255,255,0.3)'}`,
                  background: isSelected ? 'var(--primary)' : 'transparent',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: '10px',
                  color: '#ffffff',
                  fontWeight: 700,
                }}>
                  {isSelected ? '✓' : ''}
                </span>
              ) : locked && (
                <span style={{
                  position: 'absolute',
                  top: '10px',
                  right: '12px',
                  fontSize: '14px',
                  opacity: 0.8,
                }}>
                  🔒
                </span>
              )}
              <span style={{ fontSize: '28px', lineHeight: 1 }}>{meta.icon}</span>
              <span style={{
                fontFamily: 'var(--font-headline)',
                fontWeight: 800,
                fontSize: '16px',
                color: accent,
                lineHeight: 1.2,
              }}>
                {meta.label}
              </span>
              {pack.description && (
                <span style={{
                  fontFamily: 'var(--font-body)',
                  fontSize: '12px',
                  color: 'var(--on-surface-dim)',
                  lineHeight: 1.4,
                }}>
                  {pack.description}
                </span>
              )}
              <span style={{
                fontFamily: 'var(--font-mono)',
                fontSize: '11px',
                color: 'var(--on-surface-dim)',
                marginTop: 'auto',
              }}>
                {pack.cards?.length ?? 0} cards
              </span>
            </button>
          )
        })}
      </div>

      {/* Mix mode CTA */}
      {mixActive && selectedDecks.size >= 2 && (
        <button
          onClick={() => onMixStart(selectedPacks)}
          style={{
            background: 'var(--primary-glow)',
            border: 'none',
            borderRadius: '16px',
            padding: '20px',
            fontFamily: 'var(--font-headline)',
            fontWeight: 800,
            fontSize: '18px',
            color: '#ffffff',
            cursor: 'pointer',
            minHeight: '60px',
            width: '100%',
            marginTop: '16px',
            boxShadow: '0 0 28px var(--primary-glow)',
            transition: 'opacity 0.15s',
          }}
          onTouchStart={e => e.currentTarget.style.opacity = '0.85'}
          onTouchEnd={e => e.currentTarget.style.opacity = '1'}
        >
          Start Mix — {totalMixCards} cards
        </button>
      )}

      {upgradeReason !== null && (
        <UpgradePrompt reason={upgradeReason} mode={mode} onDismiss={() => setUpgradeReason(null)} />
      )}
    </div>
  )
}
