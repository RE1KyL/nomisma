// Boot the official ACP profile with the environment supplied by Nomisma.
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const packages = process.env.NOMISMA_DSH_MODULE_ROOT;
const overlay = process.argv[2];

try {
  if (!packages || !path.isAbsolute(packages) || !overlay || !path.isAbsolute(overlay)) {
    throw new Error('Managed package and policy paths are required');
  }
  const profileBoot = path.join(packages, '@deepseek-ai/dsh/lib/profile-boot.js');
  const environmentModule = path.join(packages, '@deepseek-ai/dsh-launch-environment/lib/index.js');
  const {runProfile} = await import(pathToFileURL(profileBoot).href);
  const {createLaunchEnvironmentSnapshot} = await import(pathToFileURL(environmentModule).href);
  const values = {...process.env};
  delete values.NOMISMA_PROVIDER_LEASE;
  await runProfile({
    profile: 'acp',
    patchFiles: [overlay],
    args: [],
    environment: createLaunchEnvironmentSnapshot([{source: 'process', values}]),
  });
} catch {
  process.stderr.write('ERROR: Nomisma managed ACP profile could not start\n');
  process.exitCode = 1;
}
