# dependencies.R -- the declared analysis stack.
#
# renv.lock is NOT hand-written. A lock file carries exact versions and package
# hashes; typing plausible-looking ones produces a file that fails to restore,
# or worse, restores something other than what it names. The lock is generated
# by snapshotting a real installation and is committed from that snapshot:
#
#   Rscript R/dependencies.R --install
#   Rscript -e 'renv::snapshot(prompt = FALSE)'
#
# After that, renv.lock is authoritative and CI restores from it. This file
# stays as the record of WHY each package is here, which a lock file cannot say.
#
# Bootstrap policy is deliberately explicit. CRAN packages are installed from
# the live CRAN mirror rather than from an incidental package-manager snapshot;
# packages whose authoritative distribution is GitHub use their upstream repos;
# cmdstanr uses Stan's official R-universe. renv then records the exact resolved
# versions / Git SHAs from the successful installation.

DEPENDENCIES <- list(

  # ---- data ----
  "data.table"   = "fast IO and by-reference aggregation; the panel builds live here",
  "arrow"        = "parquet; the lake's interchange format",
  "haven"        = "Stata/SPSS files, which is how a lot of finance data still arrives",
  "digest"       = "SHA-256 from R, so cos_data() can enforce the freeze itself",
  "jsonlite"     = "the results contract; emit.R writes through it",
  "dplyr"        = "readable transformation for the parts data.table makes opaque",
  "tidyr"        = "reshaping, mostly long/wide for event studies",

  # ---- core estimation ----
  "fixest"       = "high-dimensional FE, IV, and fast clustered SEs; the workhorse",
  "sandwich"     = "HC and HAC covariance",
  "lmtest"       = "coeftest, the standard wrapper for the above",
  "clubSandwich" = "CR2 small-sample cluster correction; matters below ~40 clusters",
  "fwildclusterboot" = "wild cluster bootstrap; the honest answer with few clusters",

  # ---- staggered adoption and parallel-trends robustness ----
  # The two-way FE estimator is biased under heterogeneous, staggered
  # treatment. A management-science panel is almost always staggered, so the
  # modern estimators are not optional extras here; TWFE alone is a referee's
  # first objection and a correct one.
  "did"          = "Callaway & Sant'Anna group-time ATT",
  "didimputation"= "Borusyak, Jaravel & Spiess imputation estimator",
  "did2s"        = "Gardner two-stage DiD",
  "HonestDiD"    = "Rambachan & Roth: how badly can parallel trends fail before the result dies",
  "synthdid"     = "Arkhangelsky et al. synthetic DiD, for few treated units",

  # ---- other designs ----
  "rdrobust"     = "RD with bias correction and robust CIs",
  "ivreg"        = "IV diagnostics fixest does not report",
  "AER"          = "weak-instrument tests",
  "plm"          = "panel unit roots and legacy panel estimators",

  # ---- heterogeneity and causal ML ----
  "grf"          = "causal forests for heterogeneous effects, pre-registered as exploratory",
  "DoubleML"     = "double/debiased ML when the control set is high-dimensional",

  # ---- Bayesian ----
  "cmdstanr"     = "Stan interface; compiles once and caches in CI",
  "posterior"    = "draws handling and convergence diagnostics",
  "bayesplot"    = "posterior predictive checks",

  # ---- reporting ----
  "broom"        = "tidy(); its column names ARE the emit contract's vocabulary",
  "modelsummary" = "regression tables that match journal conventions",
  "marginaleffects" = "contrasts and marginal effects, correctly delta-method'd",
  "ggplot2"      = "figures",
  "scales"       = "axis formatting",
  "patchwork"    = "multi-panel figure assembly",
  "renv"         = "the lock itself"
)

CRAN_REPO <- "https://cloud.r-project.org"
EXTRA_REPOS <- c(
  stan = "https://stan-dev.r-universe.dev",
  apache = "https://apache.r-universe.dev",
  CRAN = CRAN_REPO
)

# Upstream repositories documented by the package authors. renv::snapshot()
# records their resolved Git commit SHAs in the generated lock.
GITHUB_PACKAGES <- c(
  "HonestDiD" = "asheshrambachan/HonestDiD",
  "synthdid" = "synth-inference/synthdid"
)

cran_dependencies <- function() {
  setdiff(names(DEPENDENCIES), c(names(GITHUB_PACKAGES), "cmdstanr", "renv"))
}

print_dependencies <- function() {
  cat("CoScientist R stack:\n")
  for (pkg in names(DEPENDENCIES)) {
    source <- if (pkg %in% names(GITHUB_PACKAGES)) {
      paste0("github:", GITHUB_PACKAGES[[pkg]])
    } else if (identical(pkg, "cmdstanr")) {
      "stan-r-universe"
    } else {
      "CRAN"
    }
    cat(sprintf("  %-20s %-34s %s\n", pkg, source, DEPENDENCIES[[pkg]]))
  }
}

hard_dependencies <- c("Depends", "Imports", "LinkingTo")

install_dependencies <- function() {
  options(repos = EXTRA_REPOS)

  cores <- suppressWarnings(parallel::detectCores(logical = FALSE))
  ncpus <- if (is.na(cores) || cores < 2L) 1L else max(1L, cores - 1L)
  options(Ncpus = ncpus)

  bootstrap <- c("renv", "remotes")
  missing_bootstrap <- bootstrap[!vapply(
    bootstrap, requireNamespace, logical(1), quietly = TRUE
  )]
  if (length(missing_bootstrap)) {
    install.packages(
      missing_bootstrap,
      repos = CRAN_REPO,
      dependencies = hard_dependencies,
      Ncpus = ncpus
    )
  }

  # Install only hard dependencies. `dependencies = TRUE` also installs every
  # package's Suggests tree, which is not part of our declared capability
  # contract and can multiply a clean CI bootstrap dramatically. If a feature
  # needs an optional package, that package belongs explicitly in DEPENDENCIES
  # and therefore in the eventual renv.lock.
  cran <- cran_dependencies()
  missing_cran <- cran[!vapply(cran, requireNamespace, logical(1), quietly = TRUE)]
  if (length(missing_cran)) {
    install.packages(
      missing_cran,
      repos = EXTRA_REPOS,
      dependencies = hard_dependencies,
      Ncpus = ncpus
    )
  }

  for (pkg in names(GITHUB_PACKAGES)) {
    if (!requireNamespace(pkg, quietly = TRUE)) {
      remotes::install_github(
        GITHUB_PACKAGES[[pkg]],
        dependencies = hard_dependencies,
        upgrade = "never",
        repos = EXTRA_REPOS
      )
    }
  }

  if (!requireNamespace("cmdstanr", quietly = TRUE)) {
    install.packages(
      "cmdstanr",
      repos = c("https://stan-dev.r-universe.dev", CRAN_REPO),
      dependencies = hard_dependencies,
      Ncpus = ncpus
    )
  }

  missing <- names(DEPENDENCIES)[!vapply(
    names(DEPENDENCIES), requireNamespace, logical(1), quietly = TRUE
  )]
  if (length(missing)) {
    stop(sprintf(
      "required R packages failed to install: %s",
      paste(missing, collapse = ", ")
    ))
  }

  message("all declared R capabilities installed")
  message("snapshot this real environment with: renv::snapshot(prompt = FALSE)")
  invisible(TRUE)
}

if (!interactive() && "--install" %in% commandArgs(TRUE)) {
  install_dependencies()
} else {
  print_dependencies()
}
