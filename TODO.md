# TODO

## After the film set is right

### Vietnamese support

Voice and captions are English for now. Once the film set (stage, camera, actors, motion) is settled:

- **Voice:** Kokoro has no usable Vietnamese voice. Pick an engine that does (or record a human narrator
  and import the WAVs), and keep `narrate`'s up-to-date check and the loudness pass.
- **Captions and labels:** typesetting with Vietnamese diacritics (stacked marks such as ở, ữ, ặ) in every
  font the film uses: Literata and Lexend on the stage, the mono font in panes and cards. Check that line
  height and clipping leave room for the marks.
- **Callout limits:** `max_words` counts words; Vietnamese words are syllables, so a caption reads longer.
  Count by syllables or characters instead, per language.
- **Number words:** the callout number check knows English number words only. Add Vietnamese (một, hai, ba,
  mười, trăm, nghìn/ngàn, triệu, rưỡi) and the decimal comma (`70,2`).
- **Dates and clock:** a per-film locale for the clock and date labels (`29/10`, `Thứ Năm`).
- **Privacy scan:** make sure the deny pattern matches names with and without diacritics.
