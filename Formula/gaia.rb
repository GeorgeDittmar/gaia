class Gaia < Formula
  desc "A private, local, zero-telemetry personal AI assistant with a cyberpunk terminal UI"
  homepage "https://github.com/GeorgeDittmar/gaia"
  url "https://github.com/GeorgeDittmar/gaia/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "PLACEHOLDER"  # Update when first release is published
  license "Apache-2.0"
  version "0.1.0"

  depends_on "python@3.12" => [:build, :run]

  def install
    # Build from sdist using the system Python + hatchling.
    # Dependencies are installed from PyPI in the build env.
    pip install .
  end

  test do
    assert_executable bin / "gaia"
    system bin / "gaia", "--help"
  end
end
