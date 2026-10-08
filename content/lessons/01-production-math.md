---
id: production-math
title: Production math
order: 1
pages: 3                # split on '---' rules into swipeable pages
drills:
  phase: placement      # tag filter into the live puzzle set
  count: 8
  difficulty: [easy]
  pinned: []            # optional curated puzzle ids, served first
pass: {avg_points: 75, min_attempted: 8}
---
# Numbers are odds

Every hex carries a number, and it pays out whenever the two dice add up to it. Two dice make 36 outcomes, so a number's chance is how many of those 36 make it:

- **2 and 12:** 1 way each
- **3 and 11:** 2 ways
- **4 and 10:** 3 ways
- **5 and 9:** 4 ways
- **6 and 8:** 5 ways
- **7:** 6 ways, and it pays nobody. It moves the robber instead.

The dots printed under each token (pips) are exactly that count. A 6 comes up about one roll in seven (5 of 36), and so does an 8; a 7 comes up about one roll in six.

---
# Income is a sum of pips

A settlement collects one card from each adjacent hex whenever that hex's number is rolled; a city collects two. So a corner's income is the sum of the pips around it.

A corner touching a 6, a 5 and a 9 has 5 + 4 + 4 = **13 pips**: about 13/36, or 0.36 cards per roll, and a city there doubles it. On this board 13 is the best any corner can offer, because the board never puts two of the red 6/8 tokens next to each other, so no corner touches two of them.

Pips tell you how *often* you collect. They don't tell you *what* you collect: a 13-pip corner on two resources still can't build a settlement (wood, brick, sheep and wheat) by itself.

---
# Why a 6 or 8 isn't automatically safe

The robber blocks the hex it sits on, and the hex you depend on is the one an opponent will aim at.

Balanced dice change the short run, not the long run. They deal from a 36-outcome deck and make a number that just came up less likely to repeat, so streaks are rarer. Over a whole game the frequencies still follow the pips, so don't bet on a "hot" number.

There is one more protection: the **friendly robber**. While a player has 2 or fewer *visible* victory points, the robber can't be placed on any hex touching their settlements or cities. In the opening your best numbers are safe. Once you pass 2 visible points (a city or a third settlement does it) they are not.
