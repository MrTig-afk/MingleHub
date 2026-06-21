import { test, expect } from '@playwright/test'
import { startSession, playChooser, leave, endGame, freshPhone, tablePath } from './helpers.js'

const TABLE = 1

// Host-gate: resuming into round 2 (Roulette) must show a "Start Roulette round"
// button and NOT auto-start the round.
test('host-gates the Roulette round', async ({ page }) => {
  const host = freshPhone('host')
  const sess = await startSession(TABLE, [host, freshPhone('other')])
  await playChooser(sess, host) // round 1 done -> resume lands on round 2 (Roulette)

  await page.goto(tablePath(TABLE, host))
  const startBtn = page.getByRole('button', { name: /Start Roulette round/i })
  await expect(startBtn).toBeVisible()
  await startBtn.click()
  await expect(startBtn).toBeHidden() // gate dismissed -> round content shows
})

// Drop-to-1: when only the host remains, show "Waiting for players", the auto-end
// countdown, and an "End game now" button.
test('drop-to-1 shows Waiting + countdown + End game now', async ({ page }) => {
  const host = freshPhone('host')
  const other = freshPhone('other')
  const sess = await startSession(TABLE, [host, other])
  await playChooser(sess, host)
  await leave(sess, other) // non-host leaves -> 1 active player

  await page.goto(tablePath(TABLE, host))
  await expect(page.getByText(/Waiting for players/i)).toBeVisible()
  await expect(page.getByText(/Ending in/i)).toBeVisible()
  await expect(page.getByRole('button', { name: /End game now/i })).toBeVisible()
})

// New game button: on the recap of an ended game, the button must bypass the
// recap-lock and drop the SAME phone into a fresh lobby. We pin the phone id in
// localStorage so it survives the button's ?newgame=1 navigation (which drops the
// ?phone_id query param) — otherwise the reload would mint a new device id.
test('Recap "New game" button bypasses the recap-lock into a fresh lobby', async ({ page }) => {
  const host = freshPhone('host')
  const sess = await startSession(TABLE, [host, freshPhone('other')])
  await playChooser(sess, host)
  await endGame(sess, host) // ended -> a tap lands on recap

  await page.addInitScript((pid) => localStorage.setItem('minglehub_phone_id', pid), host)
  await page.goto(tablePath(TABLE, host))
  await expect(page.getByText(/Game Over/i)).toBeVisible()

  const newGameBtn = page.getByRole('button', { name: /New game/i })
  await expect(newGameBtn).toBeVisible()
  await newGameBtn.click()

  await expect(page).toHaveURL(/newgame=1/)
  await expect(page.getByText(/Game Over/i)).toBeHidden() // left recap -> fresh lobby
})
