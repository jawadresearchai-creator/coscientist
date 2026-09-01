# 00_setup.R -- the engine layer every analysis script sources first.
#
# Three jobs, each of which closes a hole that would otherwise let an analysis
# quietly stop being the analysis that was frozen.
#
#   cos_setup()   loads the freeze manifest and makes it the authority
#   cos_data()    loads a dataset ONLY if the freeze names it, and re-hashes it
#   cos_seed()    derives the RNG seed from the freeze hash
#
# The third is the least obvious and matters most for anything resampled. A
# seed chosen by the analyst is a free parameter: a bootstrap that comes out
# unfavourably can be re-run under a different seed until it does not, and no
# artefact anywhere records that this happened. Deriving the seed from the
# freeze hash makes the randomness a function of the design. Same design, same
# draws, always -- and changing the draws means changing the design, which the
# outcome lock already forbids.

.cos_env <- new.env(parent = emptyenv())

cos_require <- function(...) {
  for (pkg in c(...)) {
    if (!requireNamespace(pkg, quietly = TRUE)) {
      stop(sprintf("missing R package '%s'. renv.lock is the reproducibility claim; add it there.", pkg),
           call. = FALSE)
    }
  }
  invisible(TRUE)
}

cos_setup <- function(freeze_path = Sys.getenv("COSCIENTIST_FREEZE", "state/freeze.json"),
                      data_dir = Sys.getenv("COSCIENTIST_DATA_DIR", "data")) {
  cos_require("jsonlite")
  if (!file.exists(freeze_path)) {
    stop(sprintf(paste0("no freeze manifest at %s. An analysis runs against a ",
                        "registered design; without one there is nothing to say ",
                        "these results were confirmatory rather than found."),
                 freeze_path), call. = FALSE)
  }
  fz <- jsonlite::fromJSON(freeze_path, simplifyVector = FALSE)
  if (is.null(fz$freeze_hash) || !nzchar(fz$freeze_hash)) {
    stop("freeze manifest carries no hash; it cannot bind anything", call. = FALSE)
  }
  .cos_env$freeze <- fz
  .cos_env$data_dir <- data_dir
  .cos_env$loaded <- character()

  options(stringsAsFactors = FALSE, scipen = 999)
  cos_seed()

  message(sprintf("[coscientist] design %s frozen %s; %d dataset(s) admissible",
                  fz$freeze_id, fz$frozen_at, length(fz$dataset_hashes)))
  invisible(fz)
}

.cos_freeze <- function() {
  if (is.null(.cos_env$freeze)) stop("call cos_setup() first", call. = FALSE)
  .cos_env$freeze
}

cos_seed <- function(stream = 0L) {
  # First 7 hex digits of the freeze hash, so the seed is a deterministic
  # function of the design rather than of the analyst's mood.
  h <- .cos_freeze()$freeze_hash
  seed <- strtoi(substr(h, 1, 7), 16L) %% .Machine$integer.max
  seed <- as.integer((seed + as.integer(stream)) %% .Machine$integer.max)
  set.seed(seed, kind = "Mersenne-Twister", sample.kind = "Rejection")
  invisible(seed)
}

cos_data <- function(name, reader = NULL, ...) {
  # The mirror of the Python-side fetch. `fetch_frozen` downloads exactly the
  # datasets the freeze names -- but nothing stopped an R script reading a file
  # that happened to be sitting in data/ alongside them. One
  # read.csv("data/extra_helpful_panel.csv") and the analysis is no longer the
  # analysis that was frozen, with no artefact anywhere recording the change.
  fz <- .cos_freeze()
  hashes <- fz$dataset_hashes
  if (!name %in% names(hashes)) {
    stop(sprintf(paste0("'%s' is not a frozen dataset. The design was registered ",
                        "against: %s. Reading anything else makes this a different ",
                        "study from the one under the outcome lock."),
                 name, paste(names(hashes), collapse = ", ")), call. = FALSE)
  }
  path <- file.path(.cos_env$data_dir, name)
  if (!file.exists(path)) {
    stop(sprintf("frozen dataset '%s' is not in %s; run the Drive fetch first",
                 name, .cos_env$data_dir), call. = FALSE)
  }

  cos_require("digest")
  got <- digest::digest(file = path, algo = "sha256")
  want <- as.character(hashes[[name]])
  if (!identical(got, want)) {
    stop(sprintf(paste0("dataset '%s' hashes to %s, freeze expects %s. These are ",
                        "not the bytes the design was frozen against."),
                 name, substr(got, 1, 12), substr(want, 1, 12)), call. = FALSE)
  }

  if (is.null(reader)) {
    ext <- tolower(tools::file_ext(name))
    reader <- switch(ext,
      csv     = function(p, ...) { cos_require("data.table"); data.table::fread(p, ...) },
      gz      = function(p, ...) { cos_require("data.table"); data.table::fread(p, ...) },
      parquet = function(p, ...) { cos_require("arrow"); arrow::read_parquet(p, ...) },
      rds     = function(p, ...) readRDS(p),
      dta     = function(p, ...) { cos_require("haven"); haven::read_dta(p, ...) },
      stop(sprintf("no default reader for '.%s'; pass reader=", ext), call. = FALSE))
  }
  .cos_env$loaded <- union(.cos_env$loaded, name)
  reader(path, ...)
}

cos_loaded <- function() .cos_env$loaded

cos_figure_path <- function(name) {
  d <- Sys.getenv("COSCIENTIST_FIGURE_DIR", "results/figures")
  dir.create(d, recursive = TRUE, showWarnings = FALSE)
  file.path(d, name)
}
