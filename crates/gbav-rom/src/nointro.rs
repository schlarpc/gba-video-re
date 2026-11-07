//! The No-Intro "Nintendo - Game Boy Advance (Video)" DAT (20261006-104433), all 47 entries,
//! with each cart's family, engine and player build id as [`crate::classify`] reports them.
//!
//! The data lives in `nointro.csv` next to this file (columns
//! `name,sha1,size,serial,family,engine,build,notes`, RFC 4180 quoting) so other tooling can
//! read it too. Empty `family`/`engine`/`build` = not known (the one cart this project has not
//! seen). VX rows name the stream magic in `notes` ("VX++ stream" / "VXGB stream").

use std::sync::OnceLock;

use crate::classify::{Engine, Family};

const CSV: &str = include_str!("nointro.csv");

/// One No-Intro DAT entry plus this project's classification of it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NoIntroEntry {
    /// No-Intro game name (the ROM file stem).
    pub name: String,
    /// sha1 of the ROM, 40 lower-case hex digits.
    pub sha1: String,
    pub size: u64,
    /// Game code from the DAT ("MSKE"; "!none" for prototypes; may be empty).
    pub serial: String,
    pub family: Option<Family>,
    pub engine: Option<Engine>,
    /// Player build id (12 hex digits).
    pub build: Option<String>,
    pub notes: String,
}

/// All entries, in DAT order.
pub fn table() -> &'static [NoIntroEntry] {
    static TABLE: OnceLock<Vec<NoIntroEntry>> = OnceLock::new();
    TABLE.get_or_init(|| parse_table(CSV).expect("embedded nointro.csv is well-formed"))
}

/// Look an entry up by sha1 (hex, either case).
pub fn by_sha1(sha1: &str) -> Option<&'static NoIntroEntry> {
    table().iter().find(|e| e.sha1.eq_ignore_ascii_case(sha1))
}

/// Look an entry up by exact No-Intro name.
pub fn by_name(name: &str) -> Option<&'static NoIntroEntry> {
    table().iter().find(|e| e.name == name)
}

fn opt(s: &str) -> Option<String> {
    (!s.is_empty()).then(|| s.to_string())
}

fn parse_table(text: &str) -> Result<Vec<NoIntroEntry>, String> {
    let mut rows = parse_csv(text)?.into_iter();
    let header = rows.next().ok_or("empty table")?;
    if header
        != [
            "name", "sha1", "size", "serial", "family", "engine", "build", "notes",
        ]
    {
        return Err(format!("unexpected header {header:?}"));
    }
    rows.map(|r| {
        let [name, sha1, size, serial, family, engine, build, notes]: [String; 8] = r
            .try_into()
            .map_err(|r| format!("row with wrong column count: {r:?}"))?;
        let bad = |what: &str, v: &str| format!("{name}: bad {what} {v:?}");
        let fam = match family.as_str() {
            "" => None,
            f => Some(Family::parse(f).ok_or_else(|| bad("family", f))?),
        };
        let eng = match engine.as_str() {
            "" => None,
            e => Some(Engine::parse(e).ok_or_else(|| bad("engine", e))?),
        };
        Ok(NoIntroEntry {
            size: size.parse().map_err(|_| bad("size", &size))?,
            sha1: sha1.to_ascii_lowercase(),
            build: opt(&build),
            family: fam,
            engine: eng,
            name,
            serial,
            notes,
        })
    })
    .collect()
}

/// Minimal RFC 4180 reader: comma-separated, `"`-quoted fields with `""` escapes, LF or CRLF.
fn parse_csv(text: &str) -> Result<Vec<Vec<String>>, String> {
    let mut rows = Vec::new();
    let mut row = Vec::new();
    let mut field = String::new();
    let mut chars = text.chars().peekable();
    let mut quoted = false;
    let mut at_field_start = true;
    while let Some(c) = chars.next() {
        if quoted {
            match c {
                '"' if chars.peek() == Some(&'"') => {
                    chars.next();
                    field.push('"');
                }
                '"' => quoted = false,
                _ => field.push(c),
            }
            continue;
        }
        match c {
            '"' if at_field_start => {
                quoted = true;
                at_field_start = false;
            }
            '"' => return Err(format!("stray quote in row {}", rows.len() + 1)),
            ',' => {
                row.push(std::mem::take(&mut field));
                at_field_start = true;
            }
            '\r' if chars.peek() == Some(&'\n') => {}
            '\n' => {
                row.push(std::mem::take(&mut field));
                rows.push(std::mem::take(&mut row));
                at_field_start = true;
            }
            _ => {
                field.push(c);
                at_field_start = false;
            }
        }
    }
    if quoted {
        return Err("unterminated quote".into());
    }
    if !field.is_empty() || !row.is_empty() {
        row.push(field);
        rows.push(row);
    }
    Ok(rows)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn csv_quoting() {
        let t = "a,b,c\n\"x, y\",\"say \"\"hi\"\"\",\r\nplain,,\"\"\nlast,1,2";
        assert_eq!(
            parse_csv(t).unwrap(),
            vec![
                vec!["a", "b", "c"],
                vec!["x, y", "say \"hi\"", ""],
                vec!["plain", "", ""],
                vec!["last", "1", "2"],
            ]
        );
        assert!(parse_csv("a\"b").is_err());
        assert!(parse_csv("\"open").is_err());
    }

    #[test]
    fn embedded_table() {
        let t = table();
        assert_eq!(t.len(), 47);
        for (i, e) in t.iter().enumerate() {
            assert_eq!(e.sha1.len(), 40, "{}", e.name);
            assert!(e.sha1.bytes().all(|b| b.is_ascii_hexdigit()));
            assert!(
                e.name.starts_with("Game Boy Advance Video - "),
                "{}",
                e.name
            );
            assert!(
                t[..i].iter().all(|p| p.sha1 != e.sha1 && p.name != e.name),
                "duplicate {}",
                e.name
            );
            if let Some(b) = &e.build {
                assert_eq!(b.len(), 12);
                assert!(e.family.is_some() && e.engine.is_some());
            }
            if e.family == Some(Family::Vx) {
                assert!(e.notes.contains("VX++ stream") ^ e.notes.contains("VXGB stream"));
            }
        }
        assert_eq!(t.iter().filter(|e| e.build.is_some()).count(), 46);
        let shrek = by_name("Game Boy Advance Video - Shrek (USA) (Rev 6)").unwrap();
        assert_eq!(shrek.build.as_deref(), Some("344d68b1e9aa"));
        assert_eq!(by_sha1(&shrek.sha1.to_ascii_uppercase()), Some(shrek));
        let jimmy = by_sha1("1414c55bd3d252fdc05c0a94085f12dddf86ea31").unwrap();
        assert_eq!(
            (jimmy.family, jimmy.engine),
            (Some(Family::Ads), Some(Engine::HydrogenCrtTable))
        );
        assert!(by_sha1("0000000000000000000000000000000000000000").is_none());
    }
}
