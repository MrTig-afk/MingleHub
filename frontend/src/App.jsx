import React, { useState, useEffect, useCallback } from 'react'
import { track } from '@vercel/analytics'
import Home from './components/Home/Home'
import CardCategories from './components/CardCategories/CardCategories'
import FingerChooser from './components/FingerChooser/FingerChooser'
import CardReveal from './components/CardReveal/CardReveal'
import GameSummary from './components/GameSummary/GameSummary'
import UpgradePrompt from './components/UpgradePrompt/UpgradePrompt'
import { useCardDeck } from './hooks/useCardDeck'
import { FREE_TIER, FREE_TIER_CARD_LIMIT, FREE_TIER_SKIP_LIMIT, FREE_TIER_UNLOCKED_PACKS, UNI_UNLOCKED_PACKS } from './config'

const SCREENS = {
  HOME: 'HOME',
  CATEGORIES: 'CATEGORIES',
  CHOOSER: 'CHOOSER',
  CARD: 'CARD',
  SUMMARY: 'SUMMARY',
}

export default function App() {
  const [screen, setScreen] = useState(SCREENS.HOME)
  const [mode, setMode] = useState('party')
  const [devMode, setDevMode] = useState(false)
  const [cardsDrawn, setCardsDrawn] = useState(0)
  const [skipsUsed, setSkipsUsed] = useState(0)
  const [isMixMode, setIsMixMode] = useState(false)
  const [showUpgrade, setShowUpgrade] = useState(null) // null | 'card_limit'
  const [cardKey, setCardKey] = useState(0)

  const {
    packs, loading, error,
    currentPack, selectPack, selectMixedPacks,
    currentCard, deckIndex, remaining,
    complete, skip, newCard, redraw,
    reset, sessionStats, isDeckExhausted,
  } = useCardDeck(mode)

  const resetSession = useCallback(() => {
    setCardsDrawn(0)
    setSkipsUsed(0)
    setIsMixMode(false)
    setCardKey(0)
    setDevMode(false)
    setMode('party')
    setShowUpgrade(null)
    setScreen(SCREENS.HOME)
    reset()
  }, [reset])

  useEffect(() => {
    const onVis = () => { if (document.visibilityState === 'hidden') resetSession() }
    document.addEventListener('visibilitychange', onVis)
    window.addEventListener('pagehide', resetSession)
    return () => {
      document.removeEventListener('visibilitychange', onVis)
      window.removeEventListener('pagehide', resetSession)
    }
  }, [resetSession])

  const unlockedPacks = devMode ? null : (mode === 'university' ? UNI_UNLOCKED_PACKS : FREE_TIER_UNLOCKED_PACKS)
  const canSkip = devMode || skipsUsed < FREE_TIER_SKIP_LIMIT
  const skipsRemaining = FREE_TIER && !devMode ? Math.max(0, FREE_TIER_SKIP_LIMIT - skipsUsed) : null
  const showRedraw = devMode && remaining > 1

  const handleSelectPack = (pack) => {
    selectPack(pack)
    setSkipsUsed(0)
    track('pack_selected', { pack: pack.name })
    setScreen(SCREENS.CHOOSER)
  }

  const handleMixStart = (packsList) => {
    track('mix_started', { packs: packsList.map(p => p.id).join(',') })
    selectMixedPacks(packsList)
    setIsMixMode(true)
    setSkipsUsed(0)
    setScreen(SCREENS.CHOOSER)
  }

  const handleCardDraw = () => {
    setScreen(SCREENS.CARD)
  }

  const handleComplete = () => {
    track('card_completed', { pack: currentPack?.name })
    const next = cardsDrawn + 1
    setCardsDrawn(next)
    complete(0)
    if (FREE_TIER && !devMode && next >= FREE_TIER_CARD_LIMIT) {
      setShowUpgrade('card_limit')
    } else if (isDeckExhausted) {
      setScreen(SCREENS.SUMMARY)
    } else {
      setSkipsUsed(0)
      setScreen(SCREENS.CHOOSER)
    }
  }

  const handleSkip = () => {
    track('card_skipped', { pack: currentPack?.name })
    if (isDeckExhausted) {
      setScreen(SCREENS.SUMMARY)
    } else {
      skip()
      setScreen(SCREENS.CHOOSER)
    }
  }

  const handleNewCard = () => {
    track('card_skipped', { pack: currentPack?.name })
    const next = cardsDrawn + 1
    setCardsDrawn(next)
    setSkipsUsed(s => s + 1)
    setCardKey(k => k + 1)
    skip()
    if (FREE_TIER && !devMode && next >= FREE_TIER_CARD_LIMIT) {
      setShowUpgrade('card_limit')
    } else if (isDeckExhausted) {
      setScreen(SCREENS.SUMMARY)
    } else {
      setScreen(SCREENS.CARD)
    }
  }

  const handleRedraw = () => {
    setSkipsUsed(0)
    setCardKey(k => k + 1)
    redraw()
  }

  const handleEndGame = () => {
    setScreen(SCREENS.SUMMARY)
  }

  const handlePlayAgain = () => {
    track('game_replayed', { pack: currentPack?.name })
    reset()
    setSkipsUsed(0)
    setScreen(SCREENS.CHOOSER)
  }

  const dismissUpgrade = () => {
    setShowUpgrade(null)
    reset()
    setScreen(SCREENS.HOME)
  }

  if (error) {
    return (
      <div style={{
        minHeight: '100dvh',
        background: 'var(--bg-floor)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '20px',
        textAlign: 'center',
      }}>
        <div>
          <p style={{ fontFamily: 'var(--font-headline)', fontSize: '24px', color: 'var(--tertiary)' }}>
            Couldn't load decks
          </p>
          <p style={{ fontFamily: 'var(--font-body)', color: 'var(--on-surface-dim)', fontSize: '14px' }}>
            Check your connection and try again.
          </p>
        </div>
      </div>
    )
  }

  return (
    <>
      {devMode && screen !== SCREENS.HOME && (
        <div style={{
          position: 'fixed',
          top: 'calc(env(safe-area-inset-top, 0px) + 12px)',
          right: '16px',
          zIndex: 9999,
          fontFamily: 'var(--font-mono)', fontSize: '10px', fontWeight: 700,
          color: 'var(--tertiary)', background: 'rgba(10,10,12,0.7)',
          border: '1px solid var(--tertiary)', borderRadius: '6px',
          padding: '2px 7px', letterSpacing: '0.1em',
          pointerEvents: 'none',
        }}>DEV</div>
      )}

      {screen === SCREENS.HOME && (
        <Home
          packs={packs}
          loading={loading}
          mode={mode}
          onModeChange={setMode}
          devMode={devMode}
          onDevToggle={() => setDevMode(d => !d)}
          onStart={() => { track('game_started'); setScreen(SCREENS.CATEGORIES) }}
        />
      )}

      {screen === SCREENS.CATEGORIES && (
        <CardCategories
          packs={packs}
          onSelect={handleSelectPack}
          onMixStart={handleMixStart}
          onBack={() => setScreen(SCREENS.HOME)}
          unlockedPacks={unlockedPacks}
          devMode={devMode}
          mode={mode}
        />
      )}

      {screen === SCREENS.CHOOSER && (
        <FingerChooser
          packAccent={currentPack?.accent}
          onCardDraw={handleCardDraw}
          onBack={() => setScreen(SCREENS.CATEGORIES)}
        />
      )}

      {screen === SCREENS.CARD && (
        <>
          <CardReveal
            key={cardKey}
            card={currentCard}
            pack={currentPack}
            onComplete={handleComplete}
            onSkip={handleSkip}
            onNewCard={handleNewCard}
            onRedraw={handleRedraw}
            onEndGame={handleEndGame}
            onBack={() => setScreen(SCREENS.CHOOSER)}
            canSkip={canSkip}
            skipsRemaining={skipsRemaining}
            showRedraw={showRedraw}
            devMode={devMode}
            packCardIndex={deckIndex}
          />
          {showUpgrade !== null && (
            <UpgradePrompt reason={showUpgrade} mode={mode} onDismiss={dismissUpgrade} />
          )}
        </>
      )}

      {screen === SCREENS.SUMMARY && (
        <GameSummary
          sessionStats={sessionStats}
          pack={currentPack}
          isMixMode={isMixMode}
          onPlayAgain={handlePlayAgain}
          onHome={() => { reset(); setIsMixMode(false); setScreen(SCREENS.HOME) }}
          devMode={devMode}
        />
      )}
    </>
  )
}
