# sgparl/parse.py
import re
from datetime import datetime

from bs4 import BeautifulSoup
import pandas as pd

from sgparl.utils import (
    get_mp_name,
    count_syllables,
    calc_number_of_syllables,
    calc_number_of_sentences,
    count_words_and_characters,
)


# --- Sittings ---


def parse_sittings(metadata):
    """Parse sitting metadata into a single-row DataFrame."""
    date_string = metadata["sittingDate"]
    date_object = datetime.strptime(date_string, "%d-%m-%Y")
    date_str = date_object.strftime("%Y-%m-%d")

    # The sprs3 API does not expose the sitting start time. When it's missing,
    # leave datetime blank rather than fabricate one (duration stays blank too).
    start_time = (metadata.get("startTimeStr") or "").strip()
    if start_time:
        datetime_string = f"{date_string} {start_time}".replace("noon", "PM")
        try:
            datetime_object = datetime.strptime(datetime_string, "%d-%m-%Y %I:%M %p")
            datetime_str = datetime_object.strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            datetime_str = ""
    else:
        datetime_str = ""

    return pd.DataFrame(
        {
            "date": [date_str],
            "datetime": [datetime_str],
            "parliament": [metadata["parlimentNO"]],
            "session": [metadata["sessionNO"]],
            "volume": [metadata["volumeNO"]],
            "sittings": [metadata["sittingNO"]],
        }
    )


def extract_adjournment_time(date, topics_list):
    """Extract adjournment time from the Hansard speech content.

    Looks for 'Adjourned accordingly at X.XX pm' in the last few topics.
    Returns a time string like '9:07 PM' or None if not found.
    """
    # Search all topics from the end (adjournment text can appear in various topics)
    for topic in reversed(topics_list):
        content = topic.get("content") or ""
        match = re.search(
            r'[Aa]djourned\s+accordingly\s+at\s+(\d{1,2})[.:](\d{2})\s*(am|pm)',
            content,
            re.IGNORECASE,
        )
        if match:
            hour, minute, ampm = match.group(1), match.group(2), match.group(3).upper()
            return f"{hour}:{minute} {ampm}"
    return None


# --- Attendance ---


def parse_attendance(date, attendance_list):
    """Parse attendance list into a DataFrame."""
    return pd.DataFrame(
        {
            "date": [date] * len(attendance_list),
            "member_name": [get_mp_name(obj["mpName"]) for obj in attendance_list],
            "is_present": [obj["attendance"] for obj in attendance_list],
        }
    )


# --- Topics ---


def _topic_cid(date, order):
    return f"{date}-T-{order:03}"


def parse_topics(date, topics_list):
    """Parse topics list into a DataFrame."""
    orders = list(range(1, len(topics_list) + 1))
    topic_cids = [_topic_cid(date, order) for order in orders]

    return pd.DataFrame(
        {
            "topic_id": topic_cids,
            "date": [date] * len(topics_list),
            "topic_order": orders,
            "title": [section["title"] for section in topics_list],
            "section_type": [section["sectionType"] for section in topics_list],
        }
    )


# --- Speeches ---


def _process_content(soup):
    """Extract speakers and texts from BeautifulSoup-parsed HTML content.

    Ported from transform/speeches.py:process_content.
    """
    speakers = []
    texts = []

    for index, p in enumerate(soup.find_all("p")):
        try:
            if p.strong:
                strong_text = str(p.strong.text).strip()
                if (strong_text == "" or len(strong_text) < 3) and speakers:
                    speaker = speakers[-1]
                else:
                    speaker = strong_text
                text = str(p.find("strong").next_sibling)
                spans = p.find_all("span")
                if spans:
                    text = text + " " + " ".join(s.get_text() for s in spans)
            else:
                if speakers:
                    speaker = speakers[-1]
                else:
                    speaker = ""
                text = str(p.text)

            # Strip leading colon (speaker-name separator) but preserve
            # colons in text content (times like "1:20 pm", ratios, etc.)
            text = (text.strip()
                    .replace("\xa0", " ")
                    .replace("\t", " "))
            text = text.lstrip(":").strip()

            speakers.append(speaker)
            texts.append(text)
        except AttributeError as e:
            print(f"  Warning: parse error at paragraph {index} (no speaker tag): {e}")

    # Combine consecutive texts by same speaker.
    # Exception: oral questions start with "asked" / "to ask" — these are
    # separate speeches even when the same MP asks multiple questions.
    revised_speakers = []
    revised_texts = []
    last_speaker = None

    for index in range(len(speakers)):
        if (
            last_speaker == speakers[index]
            and not texts[index].strip().lower().startswith("asked")
            and "to ask" not in texts[index].strip().lower()[:10]
        ):
            revised_texts[-1] += " " + texts[index]
        else:
            revised_speakers.append(speakers[index])
            revised_texts.append(texts[index])
            last_speaker = speakers[index]

    return revised_speakers, revised_texts


def _clean_rows(df):
    """Clean speech text: strip HTML, remove boilerplate.

    Ported from transform/speeches.py:clean_rows.
    """
    df["text"] = df["text"].apply(
        lambda x: BeautifulSoup(x, "html.parser").get_text()
    )
    df["text"] = df["text"].str.replace("proc text", "", case=False)
    df["text"] = df["text"].str.replace(r"Page  \d+", "", regex=True)
    df["text"] = df["text"].str.replace("None", "")
    df = df[df["text"].astype(str).str.strip() != ""]
    return df


def _speech_cid(row):
    return f"{row['topic_id']}-S-{row['speech_order']:05}"


def _parse_topic_speeches(content, topic_cid):
    """Parse speeches from a single topic's HTML content."""
    soup = BeautifulSoup(content, "html.parser")
    speakers, texts = _process_content(soup)

    cleaned_speakers = [get_mp_name(speaker) for speaker in speakers]

    df = pd.DataFrame(
        {
            "date": [topic_cid[:10]] * len(speakers),
            "topic_id": [topic_cid] * len(speakers),
            "member_name_original": speakers,
            "member_name": cleaned_speakers,
            "text": texts,
        }
    )

    if len(df) > 0:
        df = _clean_rows(df)

    return df


def parse_speeches(date, topics_list):
    """Parse all speeches across all topics into a DataFrame."""
    all_dfs = []
    orders = list(range(1, len(topics_list) + 1))
    topic_cids = [_topic_cid(date, order) for order in orders]

    for index, topic in enumerate(topics_list):
        content = topic.get("content")
        if not content:
            continue
        topic_cid = topic_cids[index]
        topic_df = _parse_topic_speeches(content, topic_cid)
        if len(topic_df) > 0:
            all_dfs.append(topic_df)

    if not all_dfs:
        return pd.DataFrame(
            columns=[
                "date", "speech_id", "topic_id", "speech_order",
                "member_name_original", "member_name", "text",
                "num_words", "num_characters", "num_sentences", "num_syllables",
            ]
        )

    df = pd.concat(all_dfs, ignore_index=True)

    # Add speech order and IDs
    df["speech_order"] = list(range(1, len(df) + 1))
    df["speech_id"] = df.apply(_speech_cid, axis=1)

    # Add text metrics
    df["num_sentences"] = df["text"].apply(calc_number_of_sentences)
    df["num_syllables"] = df["text"].apply(calc_number_of_syllables)
    wc = df["text"].apply(
        lambda t: pd.Series(
            count_words_and_characters(t), index=["num_words", "num_characters"]
        )
    )
    df = pd.concat([df, wc], axis=1)

    # Final column order
    return df[
        [
            "date", "speech_id", "topic_id", "speech_order",
            "member_name_original", "member_name", "text",
            "num_words", "num_characters", "num_sentences", "num_syllables",
        ]
    ]
