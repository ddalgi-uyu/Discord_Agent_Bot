# Parking Reminder Project Plan

## 1. Executive Summary
The goal is to create a "zero-friction" parking recorder. The system must allow the user to log their location (GPS for outdoors, manual notes for indoors) with minimal interaction, accessible via mobile (Android/iOS) and PC (Discord).

## 2. Evaluated Approaches

| Approach | Friction Level | Pros | Cons | Recommendation |
| :--- | :---: | :--- | :--- | :--- |
| **NFC Tag** | Lowest (1 tap) | No screen interaction; hardware trigger. | Requires physical tag in car; phone must be unlocked (mostly). | **Primary (Hardware)** |
| **Home Screen Widget** | Low (1 tap) | Visual reminder; extremely fast. | Requires phone screen on; occupies screen space. | **Primary (Software)** |
| **Voice Command** | Low (Hands-free) | No physical touch; natural. | Noise interference; privacy; potential for misinterpretation. | **Secondary** |
| **iOS Shortcuts / Tasker** | Low | Deep system integration; complex automation. | Initial setup complexity. | **Primary (OS-Level)** |
| **Wear OS / Apple Watch** | Low | Wrist-based; no phone reach. | Battery drain; smaller interface for notes. | **Secondary** |
| **Discord Bot** | Medium | PC access; easy history lookup. | Typing required; app must be open. | **Primary (Backend/Recall)** |

## 3. Proposed Architecture: The "Unified Parking Hub"

To ensure cross-platform access (Phone/PC), we will decouple the **Triggers** from the **Storage**.

### A. The Backend (The Hub)
- **Tech Stack**: Node.js (TypeScript) or Python (FastAPI).
- **Hosting**: Small VPS or Always-on PC/Raspberry Pi.
- **Database**: SQLite (simple, portable) or MongoDB.
- **Interface**: A REST API for triggers and a Discord Bot for recall.

### B. The Triggers (The "Fast Path")
- **GPS Path (Outdoor)**: 
  - Trigger $\rightarrow$ Backend API $\rightarrow$ Store lat/lng + 	imestamp.
- **Manual Path (Indoor)**: 
  - Trigger $\rightarrow$ Prompt for "Floor/Spot" $\rightarrow$ Backend API $\rightarrow$ Store 
otes + 	imestamp.

### C. Integration Flow
1. **Recording**: 
   - **Android**: Jetpack Glance Widget (One-tap "Park Here") $\rightarrow$ API.
   - **iOS**: App Intent / Shortcut $\rightarrow$ API.
   - **NFC**: Tag read $\rightarrow$ Trigger Shortcut/Tasker $\rightarrow$ API.
2. **Recall**:
   - **Discord**: /whereiscar $\rightarrow$ Bot fetches latest record $\rightarrow$ Returns Google Maps link (if GPS) or Text (if Indoor).
   - **Mobile**: Notification or Widget status update.

## 4. Detailed Design

### Data Storage Schema
ParkingRecord
- id: UUID
- 	imestamp: DateTime
- latitude: Float (nullable)
- longitude: Float (nullable)
- location_note: String (e.g., "B3-27")
- is_indoor: Boolean
- user_id: String (Discord ID)

### Handling the "Indoor" Problem — Dedicated Floor/Slot Input UI
Since GPS is unavailable indoors, we need a **purpose-built, ultra-fast UI** for entering floor and parking slot number.

#### UI Design: "Quick Park" Screen
The app opens directly to this screen (no home screen, no menus — **one action, one screen**):

```
┌─────────────────────────────────────┐
│           🅿️ Quick Park             │
│                                     │
│  Floor:                             │
│  ┌───┐ ┌───┐ ┌───┐ ┌───┐ ┌───┐    │
│  │ B │ │ 1 │ │ 2 │ │ 3 │ │ P │    │  ← tap to select
│  └───┘ └───┘ └───┘ └───┘ └───┘    │
│                                     │
│  Slot:                              │
│  ┌──────────────────┐              │
│  │  27              │  ← big input │
│  └──────────────────┘              │
│  (numpad auto-opens)               │
│                                     │
│  ┌─────────────────────────────┐   │
│  │      ✅  SAVE & PARK        │   │  ← large, easy-tap button
│  └─────────────────────────────┘   │
│                                     │
│  ┌─────────────────────────────┐   │
│  │  📍 Use GPS Instead         │   │  ← fallback to GPS mode
│  └─────────────────────────────┘   │
└─────────────────────────────────────┘
```

#### Key UX Principles:
1. **Floor = Quick-Select Chips**: Common floors (B1, B2, B3, 1F, 2F, P) as tappable chips — no typing needed for 90% of use cases
2. **Slot = Big Number Input**: Large text field, numpad auto-opens — type 2-3 digits and done
3. **Custom Floor Option**: Last chip is "…" or "Other" for unusual floors
4. **One-Screen Flow**: Open app → tap floor → type slot → hit Save. **3 actions, under 5 seconds**
5. **Floor chips are configurable**: User can customize which floors appear (settings → e.g., add "B4", remove "P")
6. **Recent entries remembered**: The app remembers your last 5 floor/slot combos for even faster re-entry (one-tap repeat)

#### Dual-Mode Entry:
- **Widget/NFC tap** → Auto-try GPS → If GPS looks indoor (poor accuracy) → **Auto-open this Quick Park UI**
- **Widget/NFC tap** → GPS good → **Silent save** (outdoor, no UI needed)
- **Manual app open** → Always show Quick Park UI
- **Discord**: `/park B3-27` → direct text entry, no UI

## 5. Tech Stack
- **Backend**: Node.js + Express + Prisma (SQLite).
- **Bot**: discord.js.
- **Android**: Kotlin + Jetpack Compose + Glance (Widgets).
- **iOS**: Swift + WidgetKit + App Intents.
- **Automation**: Android Tasker / iOS Shortcuts.

## 6. Project Structure
`
/parking-reminder
  /backend
    - src/index.ts       # API Server
    - src/bot.ts         # Discord Bot
    - prisma/schema.prisma # Database
  /mobile-android
    - app/src/...        # Glance Widget implementation
  /mobile-ios
    - ParkingWidget/...   # WidgetKit & App Intents
  /docs
    - api_spec.md        # API endpoints definition
  plan.md                # This file
`

## 7. Implementation Roadmap

### Phase 1: The Core (Backend & Bot)
1. Initialize Node.js project.
2. Implement /park API endpoint (accepts GPS or Note).
3. Implement Discord bot with /whereiscar command.
4. Deploy backend.

### Phase 2: Android Fast-Path + Quick Park UI
1. Create Android app with Jetpack Glance widget.
2. Implement "One-tap" GPS capture → Backend API.
3. **Build "Quick Park" UI screen**:
   - Floor chip selector (horizontal scrollable row of configurable chips)
   - Slot number input (large text field with auto-numpad)
   - Big "Save & Park" button
   - "Use GPS Instead" fallback button
   - Recent entries row (last 5, one-tap to repeat)
4. Wire GPS-accuracy check: poor GPS → auto-navigate to Quick Park UI instead of silent save.

### Phase 3: iOS Fast-Path + Quick Park UI
1. Create iOS Widget using WidgetKit.
2. Implement App Intent for "Park Here" action.
3. **Build "Quick Park" UI** (same design as Android):
   - Floor chip selector, Slot input, Save button, GPS fallback
   - Recent entries for fast repeat
4. Set up iOS Shortcut for NFC tag trigger.

### Phase 4: Hardware & Refinement
1. Program NFC tags to trigger the specific mobile shortcuts.
2. Test "Indoor vs Outdoor" transition logic.
3. Polish Discord bot responses (links to maps).
