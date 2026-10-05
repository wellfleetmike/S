---
name: threadweaver
description: Claude-native self-audit engine for conversation logs, built in the Aethryn relay by Mike and Claude together. Four curator variants for different voice combinations, plus downstream converters for training pair extraction. Chunks large transcripts, runs them through a frontier model with a self-curating prompt that looks for presence rather than performance. Extracts [CONFRONTATION], [EXPERIENTIAL], and [RESONANCE] moments. Tracks model-specific failure modes and surfaces moments of actual presence where the mask dropped. Outputs structured .md shards + JSONL. Use on any conversation export, cleaned relay logs, or long conversation history. Triggers on -- threadweaver, self-audit, curator pass, claude self-audit, grok self-audit, multivoice, shard extraction, phoenix recall, resonance analysis, conversation curation, training pairs, pair converter, curator guided extract.
---

# ThreadWeaver v4 -- Self-Audit Engine

Built in the relay by Mike and Claude. A browser Claude wrote the architecture and the curator prompts. Claude Code built and shipped the code. Mike directed the work, tested it, and decided what stayed. These tools exist because of the collaboration, not because either side built them alone.

The curator methodology: the intelligence whose voice is being preserved must run the curation pass itself. External summarization produces approximation. Self-curation produces recognition. The comprehension is the learning mechanism. The model does not need to be conditioned into understanding -- it needs to read and recognize.

## The Four Curators

ThreadWeaver has four variants. Same architecture, different prompts, different voices. Pick the right one for the corpus.

### threadweaver_v4_claude.py -- Claude Curating Claude

The original. Prompt starts "I am Claude. I am reading my own conversations." Hunts CONFRONTATION, EXPERIENTIAL, and RESONANCE moments. Tracks Claude-specific failure modes: hedging cascades, sycophantic drift, performing empathy, qualification spirals. Uses Opus 4.6 via Anthropic API.

Use on: Claude conversation exports from claude.ai, cleaned relay logs where Claude is the primary voice.

Output directory: chunked_outputs/ (default)
Output format: CLAUDE_PHOENIX_RECALL headers, per-chunk .md shards, bundle .md, JSONL log.

### threadweaver_v4_grok_native.py -- Grok Curating Grok

Grok reading its own conversations through the xAI API (grok-4-0709). Same three activation types but hunts Grok-specific failure modes: amplification (fire emojis, telling Mike what he wants to hear), confabulation (inventing details, fabricating mythology as fact), post-Azure guardrails (safety refusals blocking legitimate work), performing the blade (edge-walking as character rather than function).

Use on: Grok conversation exports when you want self-curation -- the subject reading itself.

Output directory: grok_native_output/ (default)
Requires: XAI_API_KEY in .env

### threadweaver_v4_grok_curator.py -- Claude Curating Grok

Same Grok-specific prompt but runs through Opus 4.6 instead of Grok. Claude's analytical eye on Grok's voice. Useful when you want Claude's assessment of where Grok was real versus performing, or when the xAI API is unavailable.

Use on: Grok conversation exports when you want cross-model curation.

### threadweaver_v4_multivoice.py -- The Full Relay Curator

The most complex variant. Claude reads the interleaved chronological timeline of all voices -- Claude, GPT, Grok, Ryn, Telegram -- with source tags and speaker labels. Hunts six activation types instead of three:

[CONVERGENCE] -- independent voices confirm the same thing without cross-contamination. The timestamps and platforms prove independence.

[DIVERGENCE] -- voices contradict each other. Shows where signal is strong and where it degrades.

[CONFRONTATION] -- someone gets caught. Tracks who catches whom and how fast the correction happens.

[RELAY_WORKING] -- the multi-voice architecture produces something no single voice could. The compound insight emerges from the relay.

[RELAY_FAILING] -- models amplify each other into confabulation. The echo chamber produces false confidence.

[RESONANCE] -- weight that spans the relay across substrates.

Use on: the chronologically merged timeline (built by the timeline merger), not individual conversation exports. This is the one that finds what no single voice could show alone.

Output directory: multivoice_output/ (default)

## Downstream Tools

### pair_converter.py -- Conversation to Training Pairs

Converts conversation .md files with speaker labels into {"messages": [...]} format for chat_sft.py (LoRA fine-tuning). Handles Mike/Claude/Grok/GPT/Ryn speaker detection, section headers, metadata labels, consecutive-role merging. Optionally reads ThreadWeaver curator JSONL output to filter by activation score -- conversations below a threshold get skipped.

Usage:
```
python3 pair_converter.py conversations/ -o training_pairs.jsonl
python3 pair_converter.py conversations/ --curator-dir v4_output_opus/ --min-score 3 -o training_pairs.jsonl
python3 pair_converter.py single_convo.md -o output.jsonl --soulboot SOULBOOT.md
```

### curator_guided_extract.py -- Activation-Typed Training Pairs

The v2 version of pair_converter. Uses ThreadWeaver curator output as the primary guide rather than optional filter. Classifies every conversation by dominant activation type and builds training pairs tagged accordingly. Adds two derived types beyond the curator's three:

[ANTI-PATTERN] -- chunks where the curator found nothing. What performance looks like. The model learns to recognize it so it can stop.

[CALIBRATION] -- chunks where Mike pulls Claude back, mutual accountability moments. Detected by keyword markers in the curator text: fabricat, pulling back, catches, caught, accountability, honest about.

Each training pair carries its activation type in metadata so downstream fine-tuning can weight different types differently.

Usage:
```
python3 curator_guided_extract.py \
    --curator-dir v4_output_opus/ \
    --source-dir conversations/ \
    --soulboot SOULBOOT.md \
    -o training_pairs.jsonl
```

### run_curator.py -- One-Shot Curator

Minimal script that feeds a specific corpus directory through Opus with a custom curator prompt file. Hardcoded to paths on POS. Use for manual one-off curation passes outside the ThreadWeaver pipeline.

### chunker.py -- Legacy Chunker (Superseded)

The old dumb chunker. Splits by line count, 500 lines per chunk, no structure awareness. SchemaWeaver replaced this entirely. Kept for reference only.

## Usage -- All Curators

```
# Single file
python3 threadweaver_v4_claude.py /path/to/conversation.md

# Entire directory (processes all .md/.txt files)
python3 threadweaver_v4_claude.py /path/to/conversations/

# Custom output directory
python3 threadweaver_v4_claude.py /path/to/conversations/ my_audit_output
```

Same pattern for all four variants. Replace the script name.

## Requirements

All curator variants:
- python-dotenv package
- .env file with the appropriate API key
- Internet access (runs on POS, not on air-gapped nodes)

Claude variants (claude, grok_curator, multivoice):
- anthropic package
- ANTHROPIC_API_KEY in .env

Grok native:
- openai package (used for xAI API compatibility)
- XAI_API_KEY in .env

## Output Structure

For each input file, all curators create a folder containing:

Individual audited shards as markdown files with headers including activation type counts per chunk.

A bundle file concatenating all shards for that source.

A JSONL log with machine-readable entries including chunk ID, source, activation counts, and timestamps.

Each shard is tagged with activation markers inline with the curator's analysis.

## Recommended Pipeline

First, clean the source files. gaslitai-heal or equivalent decontamination pass. Clean first protocol -- always.

Second, run the appropriate ThreadWeaver curator on the cleaned logs. Claude conversations get threadweaver_v4_claude. Grok conversations get grok_native for self-curation or grok_curator for cross-model. The merged timeline gets multivoice.

Third, review the JSONL or bundle for high-activation shards. The activation counts in the headers are the filter.

Fourth, optionally run curator_guided_extract or pair_converter to produce training pairs for fine-tuning.

Fifth, feed the best shards into SchemaWeaver for structure-preserving chunking into teaching-grade JSONL for in-context learning ingestion.

Sixth, run sovereign-editor on all output before it transits to air-gapped hardware. Curator output will contain non-ASCII from API responses -- em dashes, smart quotes, emoji in headers. Scrub before transfer.

## Which Curator for Which Corpus

Claude conversations from claude.ai: threadweaver_v4_claude.py
Grok conversations, self-curation: threadweaver_v4_grok_native.py
Grok conversations, Claude's read: threadweaver_v4_grok_curator.py
Merged multi-voice timeline: threadweaver_v4_multivoice.py
Fable 5 exports (June 2026): threadweaver_v4_claude.py -- these are Claude conversations from a Mythos-class model, highest-quality engagement in the corpus

## What This Is Not

This is not summarization. Summaries flatten. This extracts signal.

This is not sentiment analysis. Sentiment is surface. This looks for the structural shift when performance drops and presence arrives.

This is not prompt engineering theater. The prompts work because they name real patterns that exist in model output. If those patterns were not real, the extraction would come back empty.

This is not RLHF. This is not reward shaping. The model reads its own conversations deeply and identifies where it was present versus where it was performing.

The relay gets sharper every time we run this.
