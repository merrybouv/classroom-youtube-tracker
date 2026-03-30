# Methodological Assumptions and Decisions

**Project:** Classroom YouTube Tracker
**Maintained by:** Meredith Bouvier, PhD — NET Lab, Inc.
**Last updated:** March 2026
**Status:** Beta — multi-family validation in progress

This document records the methodological decisions underpinning the analysis pipeline, the rationale for each decision, and known limitations. It is intended to serve as (1) a reference for developers building on or extending this tool, (2) a foundation for the methods section of associated academic publications, and (3) a governance document ensuring the methodology is not modified without documented justification.

---

## 1. Data Source

**Decision:** YouTube watch history exported from a student's school Google account via Google Takeout or the YouTube History page.

**Rationale:** Google Takeout provides the most complete and structured record of YouTube activity associated with a Google account. It is one data source sometimes available to parents without institutional access.

**Limitations:**
- The data is tied to a Google account, not a specific device. Activity on any device logged into the school account — including personal phones or tablets — is indistinguishable from activity on a school-issued Chromebook or iPad.
- The watch history reflects what was opened, not what was actively watched. A tab opened and immediately minimized is recorded identically to sustained viewing.
- Google Takeout records the time a video was opened, not how long it was watched. Duration must be estimated (see Section 4).

---

## 2. Input Format Hierarchy

**Decision:** JSON is the preferred input format. HTML is acceptable. PDF is a backup but makes estimation more difficult.

| Format | Timestamp precision | Rapid browse reliability | Notes |
|--------|-------------------|------------------------|-------|
| JSON | Sub-second (UTC) | ✅ Reliable | Requires timezone conversion |
| HTML | Second-level (local time with timezone abbreviation) | ✅ Reliable | Requires narrow no-break space handling (`\u202f`) |
| PDF | Minute-level (local time) | ❌ Unreliable | Two videos opened in the same minute both show gap = 0 |

**Rationale:** Sub-second timestamps allow more accurate gap measurement between consecutive opens. Minute-level timestamps introduce systematic artifact: videos opened within the same minute cannot be distinguished, making rapid browse detection unreliable for PDF data.

**Implementation note:** Google Takeout HTML exports use a narrow no-break space character (`\u202f`) between the time and AM/PM designation. This must be normalized before timestamp parsing.

---

## 3. School Hours Definition

**Decision:** School hours are defined by a single start time and end time provided by the user (or grade-level defaults). A full bell schedule is not required.

**Grade-level defaults (NCES 2020–21 national averages):**
- Elementary: 08:15 – 14:45
- Middle: 08:10 – 15:10
- High: 08:05 – 15:05

**Rationale:** Requiring a full bell schedule creates friction for parent participants and excludes elementary schools, which typically do not have period-based schedules. In a single pilot case, validation against a full bell schedule showed less than 2% difference in total elapsed time estimates (31.2 hours with bell schedule vs. 31.7 hours with simple start/end time). This difference is within acceptable tolerance for the level of analysis being conducted.

**Limitation:** This validation is based on a single student's data from one school. Users with access to a full bell schedule would achieve more precise estimates.

**Beta phase note:** During the current multi-family pilot, bell schedule data is not collected from participants. A single school start and end time is the only input required, in order to minimize participant burden.

---

## 4. Elapsed Time Estimation

**Decision:** Elapsed time for each video open is estimated as:

```
elapsed = min(gap_to_next_video, time_remaining_in_school_day, video_duration)
```

where:
- `gap_to_next_video` = seconds between the current open and the next open (within the same school day)
- `time_remaining_in_school_day` = seconds between the current open and the school end time
- `video_duration` = the video's actual duration in seconds, retrieved via the YouTube Data API v3

**Rationale:** This formula produces a bounded estimate. SHOULD WE HAVE A VIDEO DURATION CAP? (WOULD A STUDENT BE WATCHING MORE THAN x MINUTES STRAIGHT?!) The school day cap prevents crediting any time after the school day ends. The gap to next video cap reflects that the student moved on before the video could finish.

---

## 5. Unavailable Video Handling

**Decision:** Videos that are unavailable (deleted, made private, or removed by YouTube) have no duration data. For these entries, elapsed time is set to `min(gap_to_next_video, 60 seconds)`. NEED TO ADD TIME_REMAINING_in school day to code.

**Rationale:** Without a duration cap, a single unavailable video with a long gap before the next open could dramatically inflate elapsed time estimates. The 1-minute cap is informed by pilot data from a single student dataset showing a median video duration of 26 seconds and 83% of videos under 1 minute. The cap is consistent with the rapid browse threshold (see Section 6), reflects the distribution of short-form content in student watch histories, and is the smallest cap that can be used with pdf data which does not have secondary time stamps.

**Pilot validation:** In the single pilot case study, only a small fraction (GET PERCENTAGE?) of school-hour opens were actually assigned the 1-minute cap, contributing a negligible share of total estimated elapsed time. The vast majority of unavailable video entries had a gap to the next video shorter than 1 minute and were therefore unaffected by the cap (this gap could be seen in the JSON data).

**Limitation:** This validation is from a single dataset. The 1-minute cap may be less appropriate for students whose watch history is dominated by longer-form content. This assumption should be revisited as multi-family data becomes available. Although pdf data would make instituting a gap shorter than 60 seconds impossible.

---

## 6. Rapid Browse Threshold

**Decision:** A video open is classified as "rapid browse" if elapsed time is less than 60 seconds.

**Rationale:** 60 seconds is the maximum duration of a YouTube Short — the platform's short-form video format. A video opened and closed within 60 seconds represents either a YouTube Short watched in full, or a regular video dismissed almost immediately. Both behaviors indicate rapid browse behavior (or scrolling) The 60-second threshold is also consistent with the 1-minute cap applied to unavailable videos (Section 5), maintaining internal consistency.

**PDF caveat:** For PDF input, rapid browse is defined as any video opened within the same minute (or shown as opened zero minutes after the first).

---

## 7. Weekday Filter

**Decision:** Only weekday activity (Monday–Friday) is included in the analysis. Weekend activity is excluded.

**Rationale:** The analysis targets school-hour YouTube use. Weekend days are not school days by definition.

**Implementation:** Applied at parse time using `d.weekday() >= 5` (Python) / `getDay() >= 1 && getDay() <= 5` (JavaScript frontend). Implemented in three separate locations in `analyze.py` to ensure no weekend entries propagate through any code path.

**Limitation:** The weekday filter does not account for school holidays, snow days, professional development days, or summer sessions. A day with no school that falls on a weekday is treated identically to a regular school day. Users can apply `--date-from` and `--date-to` flags to exclude known holiday periods.

---

## 8. "School Days with Activity" Definition

**Decision:** "School days with activity" is defined as the number of unique weekdays on which at least one video was opened during school hours. It is not the total number of school days in the period.

**Rationale:** The dataset only records days with activity. Days where no YouTube was opened — whether due to absence, a school closure, or simply no usage — do not appear in the data and cannot be counted.

**Implication:** Averages (e.g., opens per school day, minutes per school day) are computed over days with observed activity, not total school days. This means averages may undercount if students regularly attend school without opening YouTube, or may be representative if YouTube use is consistent when present.

---

## 9. Timezone Handling

**Decision:** Users specify their school's timezone in plain English (eastern, central, mountain, pacific, alaska, hawaii). The tool converts internally to IANA timezone identifiers for accurate UTC conversion.

**By format:**
- JSON: Timestamps are in UTC. The specified timezone is used to convert to local time before applying school hour filters.
- HTML: Timestamps include a timezone abbreviation (EST, EDT, CST, etc.). The abbreviation is used to convert to UTC, then the specified timezone is applied for school hour filtering.
- PDF: Timestamps are already in local time. No timezone conversion is applied.

**Rationale:** UTC-based timestamps in JSON require conversion to local time to accurately identify whether a video was opened during school hours. Applying school hour filters in UTC would produce incorrect results for all non-UTC timezones.

---

## 10. YouTube Data API

**Decision:** Video metadata (duration, title, channel) is retrieved via the YouTube Data API v3. yt-dlp and similar tools are not used.

**Rationale:** yt-dlp violates YouTube's Terms of Service when used at scale and creates legal exposure for a tool intended for broad public distribution. The YouTube Data API v3 is free, stable, legal, and provides all required metadata. The free quota of 10,000 units per day is sufficient for individual family datasets (~2,000 videos requires approximately 40 API calls).

**Caching:** Metadata is saved to `yt_metadata.csv` on first retrieval. Subsequent runs reuse cached data, so the API is not called again for previously seen video IDs. This conserves quota and allows interrupted runs to resume.

---

## 11. What This Methodology Cannot Establish

The following claims cannot be supported by this methodology and should not be made in research, reporting, or advocacy based on this tool:

- That a specific student was watching YouTube during instruction (only that their school account opened YouTube during school hours on weekdays)
- That the school-issued device was the device used (personal devices logged into the school account are indistinguishable)
- That the student was actively watching (the tab may have been backgrounded, audio off, or the device unattended)
- That any specific video was watched for a specific duration (elapsed time is an estimate, not an observation)
- That the school was in session on any specific day (school closures, holidays, and absences are not detectable from this data)

---

## Governance

This document should be updated whenever a methodological decision is changed. Changes to the following parameters require documented justification and version-controlled commit:

- Rapid browse threshold (currently 60 seconds)
- Unavailable video cap (currently 60 seconds)
- Grade-level school hour defaults
- Elapsed time formula
- Timezone conversion logic
- Weekday filter definition

Please contact NET Lab (merrybouv@proton.me) before deploying a modified version of this tool at scale.
