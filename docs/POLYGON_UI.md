## The Polygon problem preparation system

The official address: [polygon.codeforces.com](polygon.codeforces.com)

The system is developed by the team of the [Codeforces](https://codeforces.com/) site.

Importing problems from Polygon is supported by many judging systems.

## The main sections of a problem in Polygon

### General info

- Input/output settings (console / files);

- TL / ML settings.

  - The settings are shared by all languages.
  - Codeforces supports limit multipliers in mashups / groups.
    
- The interactivity flag (adds the Interactor section).

- Advanced settings:

  - Very specific settings;
  
  - The copy and rename functions are the relevant ones.

### Files

- Contains all service files (NOT solutions);

- Resource files

  - Headers and other files for compiling the main files (including compiling solutions).
  
  - testlib.h
  
      - Be sure to press "auto-update";
  
      - Open it in the "View / Edit" mode and search for the needed functions.

  - Also files with LaTeX settings

- Source files

  - The validator;
  
  - The checker;
  
  - Generators.

    - There's a link to examples / instructions on generators.

- Attachments

  - Everything that doesn't belong to any of the categories above.

### Checker

- There are many standard ones

  - For integers — ncmp;
  
  - For big numbers — hcmp;
  
  - For real numbers — rcmp;
  
  - For yes/no — yesno / nyesno;
  
  - For string tokens — wcmp;
  
  - For lines — lcmp (ignores spaces) / fcmp (doesn't ignore spaces).

- For standard ones press "auto-update";

- For your own checkers add **tests**

  - The input / the participant's output / the jury's answer
  
  - The jury's correct answer can be generated from main correct;
  
  - If we make a test for the participant's WA / PE — we make a similar one for the jury's Crash.

- There are links to examples.

  - Including the source code of the standard ones.

### Validator

- Add tests

  - Add the samples as correct;
  
  - The vast majority of tests must be boundary ones;
  
  - OK tests may combine several boundary checks;
  
  - FAIL tests must check exactly one bound (at +-1 from OK)

- There are links to examples.

### Stresses

- Running solutions on a set of generated tests;

- Requires a written generator;

- The run script is similar to the test generation script;

  - An explanatory example is given.

### Invocations

- Runs a solution on all tests (regardless of the verdicts);

- Shows the following problems

  - OK too close to the limits;
  
  - TL / ML too close to the limits (from the outside);
  
  - No tests with boundary values of the input data;
  
    - Uses the information from the validator.

### Issues

- Any ideas of generators / tests;

- Any ideas of solutions (for any verdict);

- Aspects of the checker / validator.

### Commit

- Saves all the changes made;
  
- Check "Don't send email notifications"

- Discard is used to undo changes

  - Located in the problem list.

- To update to the latest version — Update working copy.

### Packages

- Building a package for importing into judging systems;

  - Doesn't work if there are uncommitted changes;

- Run with verification

  - Verification runs ALL solutions on ALL tests;

  - Fails if a solution doesn't match its verdict.

- The Standard version doesn't contain generated tests.

  - Relevant for Codeforces.

- The Full version contains generated tests.

  - Relevant for transferring to other systems.
  - Windows / Linux differ in line breaks.

### Manage Access

- Write — everything is allowed;

- Read — committing isn't allowed;

- There are additional roles, but they aren't relevant.

## A contest in Polygon

- Create it via New Contest

- Add individual problems from the list;

  - Or create a problem right from the contest menu;

- Give the authors the Write role on all problems at once;

- Allows doing "bulk" operations with all problems.