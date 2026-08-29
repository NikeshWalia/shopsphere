# ShopSphere in plain English

A guide to what this project is, what is in it, and why each piece exists.
No jargon without an explanation. If you are reading this to decide whether
the project is worth your time, the next three paragraphs are enough.

---

## What this is

**ShopSphere is an online shop, plus a large system for testing that shop.**

The shop works. You can browse products, register, log in, fill a cart, pay,
and see your orders. An admin can add products and adjust stock.

But the shop is not the point. **The testing is the point.** The shop exists so
that the testing has something real to work on. You cannot demonstrate serious
quality engineering against a toy, so the first job was building something
substantial enough to break in interesting ways.

The clearest way to see this is to count the lines of code:

| Part of the project | Lines |
| --- | --- |
| **The tests** | **11,654** |
| The backend (the shop's brain) | 7,439 |
| The frontend (what you see in the browser) | 5,128 |
| Documentation | 1,412 |
| The fake payment company | 311 |

There is more test code than application code. That is deliberate, and it is
the single fact that best describes this project.

---

## The two halves

### Half one: the shop

Four programs that run together and talk to each other.

| Piece | What it does |
| --- | --- |
| **Backend** | The rules. Works out prices, checks stock, saves orders. Answers questions from the frontend. |
| **Database** | The memory. Stores products, users, carts and orders permanently. |
| **Frontend** | The shop you actually see and click on in a browser. |
| **Payment provider** | A pretend payment company. |

That last one deserves an explanation, because it is the piece people usually
leave out.

**Why build a fake payment company?** Real payment providers always try to
work. That is a problem for testing, because the hardest bugs live in what
happens when payment *fails* — when it declines a card, or when it goes quiet
and never answers. You cannot test that against a service that behaves.

So this project includes a payment provider that can be *told* to fail. Ask it
to time out and it will hang. Ask it to decline and it declines. That turns
"what happens if payment breaks?" from a question nobody can answer into a test
that runs on every code change.

### Half two: the testing

Seven separate sets of tests. **916 tests in total.** Each set answers a
question the others cannot.

| Test set | How many | The question it answers |
| --- | --- | --- |
| **API** | 144 | When the frontend asks the backend a question, is the answer right? |
| **Unit** | 109 | Are the individual calculations correct — prices, totals, rounding? |
| **Security** | 61 | Can someone see or change data that is not theirs? |
| **User interface** | 55 | Can a real person actually complete a purchase in a browser? |
| **Database** | 31 | Does the database itself refuse to store nonsense? |
| **Contract** | 25 | Does the API's published documentation match what it really does? |
| **Integration** | 17 | Do all four programs work together, including when one fails? |

**Why split them up instead of just testing through the browser?** Because
browser tests are slow, and when one fails it rarely tells you *why*. If a
price is wrong, a unit test points at the exact calculation in under a second.
A browser test just says "the page showed the wrong number" after 30 seconds of
clicking.

Knowing which layer a given check belongs in is most of the skill in this job.
Putting everything in the browser is the beginner's mistake.

---

## The bugs this found

Five real bugs were found in this application by these tests, and all five were
fixed. This is the evidence that the testing does something. A test suite that
never catches anything is decoration.

The most interesting one:

### Two people bought the last item and both succeeded

One unit left in stock. Two customers check out at the exact same moment. Both
orders went through. Stock became **minus one**.

The code was already trying to prevent this. It told the database to lock the
row so that the second customer had to wait. **The lock was working.** But the
backend uses a tool called SQLAlchemy that keeps a private copy of anything it
has already read, to avoid asking the database twice. So the second checkout
locked the row correctly, then read its own stale copy from memory instead of
the fresh value. Both customers saw "1 in stock". Both bought it.

The fix was one line telling SQLAlchemy to discard its copy and use what the
database actually returned.

This is worth understanding properly, because it is a bug that looks impossible
until you know the cause. The safety measure was present and functioning. The
data was still wrong.

**The other four:** a specific invalid character crashed the server instead of
being rejected politely; a malformed stored password could crash the login
process; one error message could never actually be shown to a user; and a
customer could get stuck on the cart page with no way out.

---

## Everything else in the box

| Thing | What it is for |
| --- | --- |
| **Docker** | Packages all four programs so anyone can start the whole system with one command. |
| **Automated checks (CI)** | Every time code is pushed to GitHub, all 916 tests run automatically on a clean machine. |
| **Load testing** | Simulates many customers at once to see where the system slows down. |
| **Reporting** | Produces a browsable report. Failed browser tests include a video and a step-by-step recording. |
| **Code quality tools** | Automatically check formatting, unused code and type mistakes. |

---

## Honest limitations

This project does not claim to be finished or flawless, and the README lists
its gaps in detail. The most significant one:

**If the payment provider goes quiet mid-purchase**, the order is left waiting
and the stock stays reserved. A person has to sort it out. A real shop would
have an automatic process that checks with the payment company and settles it.

Others are deliberate trade-offs rather than oversights: logging out does not
instantly cancel your access token, product search would be too slow at very
large scale, and the performance numbers came from one laptop so they are a
baseline for spotting slowdowns, not a claim about capacity.

Listing these is intentional. Any real system has limits, and knowing your own
is more valuable than pretending you have none.

---

## Where to look

| If you want | Read |
| --- | --- |
| The full technical README | [`README.md`](../README.md) |
| How the pieces fit together | [`docs/architecture.md`](architecture.md) |
| Why the tests are organised this way | [`docs/test-strategy.md`](test-strategy.md) |
| How the automated checks work | [`docs/ci-cd.md`](ci-cd.md) |
| How failures are simulated | [`docs/failure-simulation.md`](failure-simulation.md) |
| When something will not start | [`docs/troubleshooting.md`](troubleshooting.md) |
| **The oversell bug, explained in the code** | `backend/app/services/inventory.py` |
