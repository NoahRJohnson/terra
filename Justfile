#!/usr/bin/env bash

source "${VSI_COMMON_DIR}/linux/just_env" "$(dirname "${BASH_SOURCE[0]}")"/'terra'.env
cd "${TERRA_CWD}"

# Plugins
source "${VSI_COMMON_DIR}/linux/docker_functions.bsh"
source "${VSI_COMMON_DIR}/linux/just_docker_functions.bsh"
source "${VSI_COMMON_DIR}/linux/just_git_functions.bsh"
source "${VSI_COMMON_DIR}/linux/colors.bsh"

function Pipenv()
{
  local rv=0
  PIPENV_PIPFILE="${TERRA_CWD}/Pipfile" pipenv ${@+"${@}"} || rv=$?
  return $rv
}

# Main function
function caseify()
{
  local just_arg=$1
  shift 1
  case ${just_arg} in
    build) # Build Docker image
      if [ "$#" -gt "0" ]; then
        Docker-compose "${just_arg}" ${@+"${@}"}
        extra_args+=$#
      else
        justify build_recipes gosu tini vsi pipenv
        Docker-compose build
        justify docker-compose clean venv
        justify _post_build
        justify build dsm-desktop
      fi
      ;;
    build_dsm-desktop) # Build the dsm desktop images
      pushd "${TERRA_CWD}/external/dsm_desktop" > /dev/null
        ./run build
      popd > /dev/null
      ;;
    _post_build)
      image_name=$(docker create ${TERRA_DOCKER_REPO}:terra_${TERRA_USERNAME})
      docker cp ${image_name}:/venv/Pipfile.lock "${TERRA_CWD}/docker/Pipfile.lock"
      docker rm ${image_name}
      ;;
    run) # Run terra cli (first argument is which cli, "dsm" for example)
      # Just-docker-compose run terra ${@+"${@}"}
      Pipenv run python -m terra.apps.cli ${@+"${@}"}
      extra_args+=$#
      ;;
    run_bash) # Run bash in terra image
      Just-docker-compose run terra bash ${@+"${@}"}
      ;;
    run_compile) # Run compiler
      Just-docker-compose run compile nopipenv ${@+"${@}"}
      extra_args+=$#
      ;;
    compile) # Compile terra
      Just-docker-compose run compile
      ;;
    test) # Run unit tests
      echo "${YELLOW}Running ${GREEN}C++ ${YELLOW}Tests${NC}"
      Just-docker-compose run -w "${TERRA_BUILD_DIR_DOCKER}/${TERRA_BUILD_TYPE}" compile nopipenv ctest ${@+"${@}"}
      echo "${YELLOW}Running ${GREEN}python ${YELLOW}Tests${NC}"
      Just-docker-compose run terra python -m unittest discover "${TERRA_SOURCE_DIR_DOCKER}/terra"
      extra_args+=$#
      ;;
    pep8) # Check for pep8 compliance in ./terra
         Just-docker-compose run test bash -c \
          "if ! command -v autopep8 >& /dev/null; then
             pipenv install --dev;
           fi;
           autopep8 --indent-size 2 --recursive --exit-code --diff \
                    --global-config /src/autopep8.ini \
                    /src/terra"
      ;;
    pep8_local) # Check pep8 compliance without using docker
      if ! Pipenv run command -v autopep8 >& /dev/null; then
        Pipenv install --dev
      fi
      Pipenv run autopep8 --indent-size 2 --recursive --exit-code --diff \
                          --global-config "${TERRA_CWD}/autopep8.ini" \
                          "${TERRA_SOURCE_DIR}/terra"
      ;;
    sync) # Synchronize the many aspects of the project when new code changes \
          # are applied e.g. after "git checkout"
      if [ ! -e "${TERRA_CWD}/.just_synced" ]; then
        # Add any commands here, like initializing a database, etc... that need
        # to be run the first time sync is run.
        touch "${TERRA_CWD}/.just_synced"
      fi
      Docker-compose down
      justify git_submodule-update # For those users who don't remember!
      justify build
      justify compile
      Pipenv install --keep-outdated
      ;;
    dev_sync) # Developer's extra sync
      Pipenv install --dev --keep-outdated
      ;;
    clean_all) # Delete all local volumes
      ask_question "Are you sure? This will remove packages not in Pipfile!" n
      justify docker-compose clean venv \
              docker-compose clean terra-install \
              docker-compose clean terra-build
      ;;
    ipykernel) # Start a jupyter kernel in runserver
      # Example kernel.json
      # {
      # "display_name": "terra",
      # "argv": [
      #  "python", "-m", "docker_proxy_kernel",
      #  "-f", "{connection_file}",
      #  "--cmd", "['/home/noah/git/terra/external/vsi_common/linux/just', 'ipykernel']"
      # ],
      # "env": {"JUSTFILE": "/home/noah/git/terra/Justfile"},
      # "language": "python"
      # }
      Just-docker-compose run -T --service-ports ipykernel \
          pipenv run python -m ipykernel_launcher ${@+"${@}"} > /dev/null
      extra_args+=$#
      ;;
    *)
      defaultify "${just_arg}" ${@+"${@}"}
      ;;
  esac
}

if ! command -v justify &> /dev/null; then caseify ${@+"${@}"};fi
