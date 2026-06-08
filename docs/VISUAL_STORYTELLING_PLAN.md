# Visual Storytelling Plan

Updated: 2026-06-08

## Purpose

The app should explain a decision before it exposes implementation detail. Every visual
must help answer one of four questions:

1. What changed?
2. Why does it matter?
3. What could block the action?
4. Did the decision work later?

## Visual Grammar

- Blue means evidence or measurement.
- Amber means caution, uncertainty, or a gate that needs review.
- Green means a recorded paper action or completed learning step.
- Red is reserved for a failed safety gate, not merely a weak prediction.
- Motion shows direction or feedback. It must never imply confidence by itself.

## Architecture Views

### Start Here

A five-stage story: Observe, Explain, Guard, Decide, Learn. This is the default view and
must fit into one phone screen at a time.

### Variables

An interactive decoder organized by six questions: Direction, Consistency, Risk,
Tradability, Evidence, and Context.

### Technical Detail

The implementation pipeline, validation boundaries, model versions, and known limits.
This view is for development and audit work, not first-time orientation.

## Processing Studies

Processing is a design lab, not a production dependency. Prototype these studies:

1. Candidate particles moving through quality, risk, and account gates.
2. A ranking river showing stocks entering, persisting in, and leaving the watchlist.
3. A feedback loop that connects each paper decision to 1d, 5d, 20d, and 60d outcomes.
4. A model tournament where baseline and challenger paths separate by horizon.

Export useful studies as MP4, GIF, or frame sequences. Rebuild only the clearest ideas
with web-native HTML, CSS, Plotly, or a Streamlit component.

## Acceptance Tests

- A new user can explain the five stages after 30 seconds.
- A phone user can reach the variable decoder or technical pipeline in two taps.
- Every chart has a plain-language title and one sentence explaining how to read it.
- Animation respects reduced-motion settings and does not encode an unsupported claim.
