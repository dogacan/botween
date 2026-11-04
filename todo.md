# Todo

Notes from Halloween 2026 (Bonesy monitored from the recordings).

- **Freeze at the start of a conversation.** At 18:26 a session opened and camera frames kept going, but nothing else happened for over a minute: no transcript, no fallback greeting, no close. The local timers stopped too, so something in the conversation loop hung (not just Gemini being slow). Needed a restart. Find where it blocks and add a watchdog that ends a session with no activity.
- **Misheard speech triggers a language switch.** An adult's speech was transcribed as Hebrew, and Bonesy answered in Hebrew with a line that didn't fit; another bit came through as German ("Mensch, Mann"). Consider requiring more than one sentence, or a confident transcript, before switching.
- **Quiet-visitor joke fires after a goodbye.** A kid said "Bye, Mr. Skeleton" and the joke still played a few seconds later. Skip the joke if the visitor's last line was a goodbye.
- **"Reply started" timing is wrong for greetings.** The note measures from the previous visitor's last words, so greetings Bonesy starts on his own show 7-67 s. Reset the timer when a session starts or a cue is sent.
- **Max-duration cutoff mid-joke.** A joke got cut at "why did I cross the road?---" when the 90 s cap hit. Maybe let the current reply finish before the goodbye.
- **Multilingual crowds.** A Hindi question got an English reply and Gujarati only half a reply; "Ni hao ma?" was answered in pinyin instead of Chinese characters (the voice may mispronounce it). A Turkish visitor asked three times and said "the skeleton doesn't understand me" even though every reply was correct Turkish, so in a loud crowd replies may be too quiet or too late to hear.
