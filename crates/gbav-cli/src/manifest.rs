//! The hash manifests in `verify/`: locating them, the cart short ids, and reading a manifest's
//! md5 column.
//!
//! Layout: `verify/carts.csv` (family, short_id, sha1, nointro) and
//! `verify/<family>/<short_id>_<stream>.{frame,audio}_md5.csv.gz`. The frame manifests'
//! first md5 column hashes what [`gbav_common::Frame::manifest_bytes`] returns; the audio
//! manifests' last md5 column hashes what [`gbav_common::AudioDecoder::next_block`] returns.

use gbav_common::{gzip, Error, Result};
use std::path::{Path, PathBuf};

/// The `verify/` directory: `$GBAV_VERIFY`, else the one in the source tree gbav was built from.
pub fn verify_dir(explicit: Option<&Path>) -> Result<PathBuf> {
    let cands: Vec<PathBuf> = explicit
        .map(Path::to_path_buf)
        .into_iter()
        .chain(std::env::var_os("GBAV_VERIFY").map(PathBuf::from))
        .chain(std::iter::once(
            Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("..").join("verify"),
        ))
        .collect();
    cands
        .iter()
        .find(|p| p.join("carts.csv").is_file())
        .cloned()
        .ok_or_else(|| Error::Invalid(format!("no verify/ directory with carts.csv (tried {cands:?}); set GBAV_VERIFY")))
}

/// One row of `verify/carts.csv`.
#[derive(Debug, Clone)]
pub struct Cart {
    pub family: String,
    pub short_id: String,
    pub sha1: String,
    pub nointro: String,
}

fn split_csv_line(line: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut cur = String::new();
    let mut quoted = false;
    let mut chars = line.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '"' if quoted && chars.peek() == Some(&'"') => {
                cur.push('"');
                chars.next();
            }
            '"' => quoted = !quoted,
            ',' if !quoted => out.push(std::mem::take(&mut cur)),
            _ => cur.push(c),
        }
    }
    out.push(cur);
    out
}

pub fn carts(dir: &Path) -> Result<Vec<Cart>> {
    let text = std::fs::read_to_string(dir.join("carts.csv"))
        .map_err(|e| Error::Invalid(format!("carts.csv: {e}")))?;
    Ok(text
        .lines()
        .skip(1)
        .filter(|l| !l.is_empty())
        .map(|l| {
            let f = split_csv_line(l);
            Cart {
                family: f[0].clone(),
                short_id: f[1].clone(),
                sha1: f[2].clone(),
                nointro: f.get(3).cloned().unwrap_or_default(),
            }
        })
        .collect())
}

/// Manifest file stem for a stream: `<short_id>_<stream id>`, spaces as underscores.
pub fn stem(short_id: &str, stream: &str) -> String {
    format!("{short_id}_{}", stream.replace(' ', "_"))
}

/// Which md5 column of a manifest to read.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Column {
    /// The first column whose header contains "md5" (frame manifests).
    FirstMd5,
    /// The last column whose header contains "md5" (audio manifests).
    LastMd5,
}

/// A manifest as (key, md5) rows in file order; the key is the first column (frame or block
/// index). `Ok(None)` if the file does not exist.
pub fn load(path: &Path, col: Column) -> Result<Option<Vec<(u64, String)>>> {
    let gz = match std::fs::read(path) {
        Ok(b) => b,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(Error::Invalid(format!("{}: {e}", path.display()))),
    };
    let text = String::from_utf8(gzip::gunzip(&gz)?)
        .map_err(|_| Error::Format(format!("{}: not UTF-8", path.display())))?;
    let mut lines = text.lines();
    let header: Vec<&str> = lines.next().unwrap_or("").split(',').collect();
    let md5cols: Vec<usize> = (0..header.len()).filter(|&i| header[i].contains("md5")).collect();
    let c = match col {
        Column::FirstMd5 => md5cols.first(),
        Column::LastMd5 => md5cols.last(),
    }
    .copied()
    .ok_or_else(|| Error::Format(format!("{}: no md5 column", path.display())))?;
    let mut rows = Vec::new();
    for l in lines.filter(|l| !l.is_empty()) {
        let f: Vec<&str> = l.split(',').collect();
        let key = f[0].parse().map_err(|_| Error::Format(format!("{}: bad key {:?}", path.display(), f[0])))?;
        rows.push((key, f.get(c).copied().unwrap_or("").to_string()));
    }
    Ok(Some(rows))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn csv_quoting() {
        assert_eq!(split_csv_line(r#"a,"b, c","d ""e""",f"#), ["a", "b, c", r#"d "e""#, "f"]);
    }

    #[test]
    fn repo_manifests() {
        let dir = verify_dir(None).unwrap();
        let carts = carts(&dir).unwrap();
        assert_eq!(carts.len(), 46);
        let rows = load(&dir.join("vx/shrek-sharktale-rev5_3dd3600.frame_md5.csv.gz"), Column::FirstMd5)
            .unwrap()
            .unwrap();
        assert_eq!(rows.len(), 595);
        assert_eq!(rows[0].0, 0);
        assert_eq!(rows[0].1.len(), 32);
        assert_eq!(stem("dbgt", "logo majesco"), "dbgt_logo_majesco");
    }
}
