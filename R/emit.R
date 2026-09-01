# emit.R -- the R half of the results contract.
#
# An R analysis script does not print numbers. It emits them, one JSON object
# per reportable quantity, into a stream that Python validates and assembles
# into the manuscript bridge. Anything printed instead of emitted cannot be
# cited, because the provenance validator has no row to match it against.
#
# Usage:
#
#   source("R/emit.R")
#   em <- emitter("R/03_primary_models.R")
#
#   m <- fixest::feols(y ~ treat_post | firm + quarter, data = panel,
#                      cluster = ~ firm)
#   em$tidy(broom::tidy(m, conf.int = TRUE),
#           keep   = "treat_post",
#           prefix = "h1_",
#           model  = "TWFE, firm + quarter FE, clustered by firm",
#           units  = "pp",
#           n_obs  = nobs(m),
#           n_clusters = m$fixef_sizes[["firm"]])
#
#   em$done()      # <- the last line of the script, and only there
#
# `done()` is what tells the collector this analysis finished. An R script that
# errors never reaches its last line, so the stream stays unsealed and the
# Python side refuses the whole bundle. That is the intended behaviour: a
# half-finished analysis assembles into something that looks complete, and
# nothing about the resulting tables would ever reveal that the models stopped
# running two thirds of the way through.

.cos_require <- function(pkg) {
  if (!requireNamespace(pkg, quietly = TRUE)) {
    stop(sprintf("emit.R needs the '%s' package; add it to renv.lock", pkg),
         call. = FALSE)
  }
}

.cos_results_dir <- function() {
  d <- Sys.getenv("COSCIENTIST_RESULTS_DIR", unset = "results/streams")
  dir.create(d, recursive = TRUE, showWarnings = FALSE)
  d
}

.cos_num <- function(x, what, token) {
  # Refuse non-finite values here as well as in Python. A model that failed to
  # converge returns NaN; carried forward it makes every downstream comparison
  # vacuously true, and a vacuous pass is worse than a failure because nothing
  # reports it.
  if (is.null(x) || length(x) == 0L) return(NULL)
  if (length(x) > 1L) {
    stop(sprintf("%s for token '%s' has length %d; one number per field",
                 what, token, length(x)), call. = FALSE)
  }
  # is.nan() BEFORE is.na(), because is.na(NaN) is TRUE in R and the NA branch
  # returns NULL. A NaN standard error -- what a singular cluster variance
  # matrix returns, which is common -- was therefore dropped exactly like an
  # absent one, and the record went out carrying an estimate with no
  # uncertainty at all. NA means "not computed"; NaN means "computed, and
  # undefined". Only the first is safe to omit.
  if (is.nan(x)) {
    stop(sprintf(paste0("%s for token '%s' is NaN -- the model returned an ",
                        "undefined value, most often a singular variance matrix. ",
                        "Dropping it would report an estimate with no uncertainty."),
                 what, token), call. = FALSE)
  }
  if (is.na(x)) return(NULL)
  x <- as.numeric(x)
  if (!is.finite(x)) {
    stop(sprintf("%s for token '%s' is %s -- the estimate is undefined, not a result",
                 what, token, format(x)), call. = FALSE)
  }
  x
}

emitter <- function(script = NULL, dir = .cos_results_dir()) {
  .cos_require("jsonlite")
  # Provenance is the runner's identity, not a string the script chooses.
  # `script` was whatever the author typed, so a record could truthfully pass
  # every check while naming a different -- real, hashed -- script as its
  # producer. The workflow exports COSCIENTIST_CURRENT_SCRIPT for the file it
  # is actually executing; when that is set it wins, and a disagreeing argument
  # is an error rather than a silent override.
  runner <- Sys.getenv("COSCIENTIST_CURRENT_SCRIPT", unset = "")
  if (nzchar(runner)) {
    if (!is.null(script) && nzchar(script) && !identical(script, runner)) {
      stop(sprintf(paste0("emitter('%s') was called while the runner is executing ",
                          "'%s'. A result cannot name a script other than the one ",
                          "producing it."), script, runner), call. = FALSE)
    }
    script <- runner
  }
  if (is.null(script) || !nzchar(script)) {
    stop(paste0("emitter() needs a script identity: run through the workflow, ",
                "which sets COSCIENTIST_CURRENT_SCRIPT, or pass script= when ",
                "running by hand."), call. = FALSE)
  }
  # Create the directory here, not in the default argument. `dir` defaulted to
  # .cos_results_dir(), which had the mkdir inside it -- so supplying `dir`
  # explicitly skipped directory creation entirely and every write failed.
  # A side effect hidden in a default argument runs only when the default does.
  dir.create(dir, recursive = TRUE, showWarnings = FALSE)
  stem <- gsub("[^A-Za-z0-9._-]+", "_", basename(script))
  path <- file.path(dir, paste0(stem, ".results.jsonl"))
  fig_path <- file.path(dir, paste0(stem, ".figures.jsonl"))

  # Truncate on creation. Appending to a previous run's stream mixes results
  # from two different executions of the code into one bundle, and nothing
  # downstream can tell which number came from which run.
  if (file.exists(path)) file.remove(path)
  if (file.exists(fig_path)) file.remove(fig_path)
  file.create(path)
  sealed <- FALSE

  write_obj <- function(obj) {
    if (sealed) {
      stop("this stream is already sealed; emit before calling done()", call. = FALSE)
    }
    # digits = NA keeps full double precision. jsonlite's default of 4 decimal
    # places would quietly round a coefficient before it ever reached the
    # manuscript, and the rounded value would then validate against itself.
    line <- jsonlite::toJSON(obj, auto_unbox = TRUE, digits = NA, na = "null")
    cat(line, "\n", sep = "", file = path, append = TRUE)
    invisible(obj)
  }

  row <- function(token, label, value, units = "", se = NULL,
                  ci_low = NULL, ci_high = NULL, p_value = NULL,
                  n_obs = NULL, n_clusters = NULL, model = "",
                  family = "primary", adjusted = "") {
    obj <- list(
      token  = as.character(token),
      label  = as.character(label),
      value  = .cos_num(value, "value", token),
      units  = as.character(units),
      script = as.character(script),
      model  = as.character(model),
      family = as.character(family),
      adjusted = as.character(adjusted)
    )
    if (is.null(obj$value)) {
      stop(sprintf("token '%s' has no value", token), call. = FALSE)
    }
    for (nm in c("se", "ci_low", "ci_high", "p_value")) {
      v <- .cos_num(get(nm), nm, token)
      if (!is.null(v)) obj[[nm]] <- v
    }
    for (nm in c("n_obs", "n_clusters")) {
      v <- .cos_num(get(nm), nm, token)
      if (!is.null(v)) obj[[nm]] <- as.integer(round(v))
    }
    write_obj(obj)
  }

  tidy <- function(tidied, keep = NULL, prefix = "", model = "", units = "",
                   family = "primary", n_obs = NULL, n_clusters = NULL,
                   adjusted = "", labels = NULL) {
    # broom::tidy() column names are the contract's native vocabulary, so a
    # fixest or lm fit goes straight through without a rename step. Renaming by
    # hand is the transcription this whole bridge exists to remove.
    if (!"term" %in% names(tidied)) {
      stop("tidy(): expected a broom tidy data frame with a 'term' column", call. = FALSE)
    }
    if (!is.null(keep)) {
      tidied <- tidied[tidied$term %in% keep, , drop = FALSE]
      absent <- setdiff(keep, tidied$term)
      if (length(absent)) {
        # Silently emitting nothing is how a "robustness check" ends up in the
        # manuscript having never been estimated.
        stop(sprintf("tidy(): term(s) not in the model: %s",
                     paste(absent, collapse = ", ")), call. = FALSE)
      }
    }
    if (nrow(tidied) == 0L) stop("tidy(): nothing to emit", call. = FALSE)

    get_col <- function(r, nm) if (nm %in% names(r)) r[[nm]] else NULL
    for (i in seq_len(nrow(tidied))) {
      r <- tidied[i, , drop = FALSE]
      term <- as.character(r$term)
      token <- paste0(prefix, gsub("[^A-Za-z0-9]+", "_", tolower(term)))
      label <- if (!is.null(labels) && term %in% names(labels)) labels[[term]] else term
      row(token = token, label = label,
          value = get_col(r, "estimate"), units = units,
          se = get_col(r, "std.error"),
          ci_low = get_col(r, "conf.low"), ci_high = get_col(r, "conf.high"),
          p_value = get_col(r, "p.value"),
          n_obs = n_obs, n_clusters = n_clusters,
          model = model, family = family, adjusted = adjusted)
    }
    invisible(nrow(tidied))
  }

  figure <- function(id, path, caption, result_tokens = character(),
                     dpi = 300, width_in = NULL, height_in = NULL, panels = 1) {
    # Figures go to their own stream. A figure is not a result record -- giving
    # it a token and a value of zero would put a fake number into the bundle
    # that the provenance validator would then happily match prose against.
    if (length(result_tokens) == 0L) {
      stop(sprintf(paste0("figure '%s' names no result tokens. A figure asserts a ",
                          "finding as surely as a sentence does; one bound to nothing ",
                          "is the only claim here that no validator can check."), id),
           call. = FALSE)
    }
    obj <- list(id = as.character(id), path = as.character(path),
                caption = as.character(caption), script = as.character(script),
                result_tokens = I(as.character(result_tokens)),
                dpi = as.integer(dpi), panels = as.integer(panels))
    if (!is.null(width_in))  obj$width_in  <- as.numeric(width_in)
    if (!is.null(height_in)) obj$height_in <- as.numeric(height_in)
    if (sealed) stop("this stream is already sealed; emit before calling done()",
                     call. = FALSE)
    line <- jsonlite::toJSON(obj, auto_unbox = TRUE, digits = NA, na = "null")
    cat(line, "\n", sep = "", file = fig_path, append = TRUE)
    invisible(obj)
  }

  done <- function() {
    write_obj(list(`__complete__` = TRUE, script = as.character(script)))
    sealed <<- TRUE
    invisible(path)
  }

  list(path = path, fig_path = fig_path,
       row = row, tidy = tidy, figure = figure, done = done)
}
